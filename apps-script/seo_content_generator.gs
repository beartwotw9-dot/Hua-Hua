/**
 * SEO Content Generator — SEO Automation MVP (1-day build)
 * --------------------------------------------------------
 * Reads the first `pending` row from `SEO_Input`, asks an LLM (or mock)
 * for a Traditional-Chinese article + 3 image prompts + quality notes
 * in a strict ===SECTION=== block format, parses it, and appends one row
 * to `SEO_Output`. Then marks the input row `done`.
 *
 * Demo mode:
 *   SEO_MOCK_MODE = true   -> no API key needed, returns canned response
 *   SEO_MOCK_MODE = false  -> calls OpenAI (default) or Anthropic via Script Properties
 *
 * Why a custom ===SECTION=== format instead of JSON mode?
 *   Apps Script demo, model-agnostic. Strict markers + a small regex parser
 *   work the same on OpenAI / Anthropic and survive minor formatting drift
 *   better than JSON mode does in a 1-day build.
 *
 * TODO (post-MVP, intentionally NOT in scope for this demo):
 *   - Add retry / exponential backoff on 5xx
 *   - Switch to JSON mode + schema validation
 *   - Generate the actual images (not just prompts)
 *   - Dedupe by (keyword, product, scenario) before appending
 */

// ---- Config (script-global; SEO_ prefix avoids collision with
//      serp_entity_analyzer.gs in the same Apps Script project) ----
var SEO_MOCK_MODE = true;
var SEO_INPUT_SHEET = 'SEO_Input';
var SEO_OUTPUT_SHEET = 'SEO_Output';

// Header order for SEO_Output. appendRow writes positionally, so this
// must match the sheet exactly. Validated at runtime by assertSeoHeaders_.
var SEO_OUTPUT_HEADERS = [
  'timestamp', 'input_row', 'keyword', 'product', 'scenario',
  'article', 'image_prompt_25', 'image_prompt_50', 'image_prompt_75',
  'quality_notes', 'mode'
];

function generateSeoContent() {
  var mode = SEO_MOCK_MODE ? 'MOCK' : 'LIVE';
  logSeo_('--- generateSeoContent start (mode=' + mode + ') ---');

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var inputSheet = getRequiredSeoSheet_(ss, SEO_INPUT_SHEET);
  var outputSheet = getRequiredSeoSheet_(ss, SEO_OUTPUT_SHEET);
  assertSeoHeaders_(outputSheet, SEO_OUTPUT_HEADERS);

  var pending = findFirstPendingSeoRow_(inputSheet);
  if (!pending) {
    logSeo_('No pending SEO rows. Nothing to do.');
    return;
  }

  var rowNumber = pending.rowNumber;
  var row = pending.row;
  var headers = pending.headers;
  var payload = {
    keyword: String(readSeoByHeader_(row, headers, 'keyword') || '').trim(),
    product: String(readSeoByHeader_(row, headers, 'product') || '').trim(),
    scenario: String(readSeoByHeader_(row, headers, 'scenario') || '').trim()
  };

  if (!payload.keyword) {
    markSeoStatus_(inputSheet, headers, rowNumber, 'error', 'Missing keyword');
    throw new Error('SEO_Input row ' + rowNumber + ' is missing keyword.');
  }
  logSeo_('Picked row ' + rowNumber + ' keyword="' + payload.keyword +
          '" product="' + payload.product + '" scenario="' + payload.scenario + '"');

  try {
    markSeoStatus_(inputSheet, headers, rowNumber, 'processing', '');
    var prompt = buildSeoPrompt_(payload);
    var rawResponse = SEO_MOCK_MODE ? getMockSeoResponse_(payload) : callLlm_(prompt);
    var sections = parseSections_(rawResponse);

    outputSheet.appendRow([
      new Date(),
      rowNumber,
      payload.keyword,
      payload.product,
      payload.scenario,
      sections.article,
      sections.image_prompt_25,
      sections.image_prompt_50,
      sections.image_prompt_75,
      sections.quality_notes,
      SEO_MOCK_MODE ? 'mock' : 'live'
    ]);

    markSeoStatus_(inputSheet, headers, rowNumber, 'done', '');
    logSeo_('Wrote 1 row to ' + SEO_OUTPUT_SHEET +
            ' (article ' + (sections.article || '').length + ' chars), ' +
            'marked input row ' + rowNumber + ' as done.');
  } catch (error) {
    markSeoStatus_(inputSheet, headers, rowNumber, 'error', safeSeoErrorMessage_(error));
    logSeo_('ERROR on row ' + rowNumber + ': ' + safeSeoErrorMessage_(error));
    throw error;
  }
}

function buildSeoPrompt_(payload) {
  return [
    'You are an SEO content assistant for Traditional Chinese content.',
    'Return all sections exactly in this format:',
    '=== ARTICLE ===',
    '=== IMAGE_PROMPT_25 ===',
    '=== IMAGE_PROMPT_50 ===',
    '=== IMAGE_PROMPT_75 ===',
    '=== QUALITY_NOTES ===',
    '',
    'Keyword: ' + payload.keyword,
    'Product: ' + (payload.product || 'N/A'),
    'Scenario: ' + (payload.scenario || 'N/A'),
    '',
    'Requirements:',
    '- Write a practical article in Traditional Chinese.',
    '- Mention the keyword naturally.',
    '- Mention the product and scenario when relevant.',
    '- Keep quality notes concise and actionable.',
    '- Image prompts should describe scenes for hero image, mid-article image, and closing image.'
  ].join('\n');
}

function callLlm_(prompt) {
  var props = PropertiesService.getScriptProperties();
  var provider = String(props.getProperty('LLM_PROVIDER') || 'openai').toLowerCase();
  var apiKey = String(props.getProperty('LLM_API_KEY') || '').trim();
  if (!apiKey) {
    throw new Error('LLM_API_KEY is required when SEO_MOCK_MODE=false.');
  }

  if (provider === 'anthropic') {
    return callAnthropic_(apiKey, prompt);
  }
  return callOpenAi_(apiKey, prompt);
}

function callOpenAi_(apiKey, prompt) {
  var response = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    muteHttpExceptions: true,
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + apiKey },
    payload: JSON.stringify({
      model: 'gpt-4o-mini',
      temperature: 0.7,
      messages: [
        { role: 'system', content: 'Follow the requested section format exactly.' },
        { role: 'user', content: prompt }
      ]
    })
  });

  if (response.getResponseCode() >= 300) {
    throw new Error('OpenAI error ' + response.getResponseCode() + ': ' + response.getContentText());
  }

  var json = JSON.parse(response.getContentText());
  return (((json.choices || [])[0] || {}).message || {}).content || '';
}

function callAnthropic_(apiKey, prompt) {
  var response = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'post',
    muteHttpExceptions: true,
    contentType: 'application/json',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01'
    },
    payload: JSON.stringify({
      model: 'claude-haiku-4-5',
      max_tokens: 1800,
      temperature: 0.7,
      messages: [
        { role: 'user', content: prompt }
      ]
    })
  });

  if (response.getResponseCode() >= 300) {
    throw new Error('Anthropic error ' + response.getResponseCode() + ': ' + response.getContentText());
  }

  var json = JSON.parse(response.getContentText());
  return (((json.content || [])[0] || {}).text) || '';
}

function getMockSeoResponse_(payload) {
  return [
    '=== ARTICLE ===',
    '# ' + payload.keyword + '選購指南',
    '',
    '如果你正在找 ' + payload.keyword + '，先確認毛孩的年齡、體型與活動量，再搭配實際預算做篩選。',
    '以 ' + (payload.product || '目標產品') + ' 為例，若情境是 ' + (payload.scenario || '一般家庭日常') + '，可以優先比較蛋白質來源、顆粒大小與適口性。',
    '挑選時建議先從單一變因開始測試，例如先固定主食，再觀察便便、精神與食慾 7 到 10 天。',
    '最後，把價格、成分與回購便利性一起看，才能找到長期可持續的選擇。',
    '',
    '=== IMAGE_PROMPT_25 ===',
    'A bright editorial product scene showing pet food, measuring scoop, and ingredient notes on a clean table, warm daylight, realistic photography',
    '',
    '=== IMAGE_PROMPT_50 ===',
    'A cozy indoor small-dog feeding moment with premium kibble bowl, owner comparing labels, natural home setting, magazine-style composition',
    '',
    '=== IMAGE_PROMPT_75 ===',
    'A reassuring closing scene with a healthy happy dog beside neatly stored pet food bag, soft afternoon light, lifestyle photography',
    '',
    '=== QUALITY_NOTES ===',
    '- 已自然置入關鍵字',
    '- 已納入產品與使用情境',
    '- 建議上線前補上品牌實測或價格比較資料'
  ].join('\n');
}

function parseSections_(text) {
  var markers = [
    'ARTICLE',
    'IMAGE_PROMPT_25',
    'IMAGE_PROMPT_50',
    'IMAGE_PROMPT_75',
    'QUALITY_NOTES'
  ];
  var sections = {};

  markers.forEach(function(marker, index) {
    var nextMarker = index + 1 < markers.length ? markers[index + 1] : null;
    var pattern = nextMarker
      ? new RegExp('===\\s*' + marker + '\\s*===([\\s\\S]*?)===\\s*' + nextMarker + '\\s*===')
      : new RegExp('===\\s*' + marker + '\\s*===([\\s\\S]*)$');
    var match = String(text || '').match(pattern);
    sections[marker.toLowerCase()] = match ? String(match[1]).trim() : '';
  });

  if (!sections.article) {
    throw new Error('Failed to parse ARTICLE section from LLM response.');
  }
  return sections;
}

function getRequiredSeoSheet_(ss, sheetName) {
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) {
    throw new Error('Missing sheet: ' + sheetName);
  }
  return sheet;
}

function findFirstPendingSeoRow_(sheet) {
  var values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    return null;
  }
  var headers = values[0];
  var statusIndex = findSeoHeaderIndex_(headers, 'status');
  if (statusIndex === -1) {
    throw new Error('Missing "status" header in ' + sheet.getName());
  }
  for (var i = 1; i < values.length; i++) {
    var status = String(values[i][statusIndex] || '').trim().toLowerCase();
    if (status === 'pending') {
      return { rowNumber: i + 1, row: values[i], headers: headers };
    }
  }
  return null;
}

function markSeoStatus_(sheet, headers, rowNumber, status, errorMessage) {
  var statusIndex = findSeoHeaderIndex_(headers, 'status');
  if (statusIndex !== -1) {
    sheet.getRange(rowNumber, statusIndex + 1).setValue(status);
  }
  var errorIndex = findSeoHeaderIndex_(headers, 'error_message');
  if (errorIndex !== -1) {
    sheet.getRange(rowNumber, errorIndex + 1).setValue(errorMessage || '');
  }
  var updatedAtIndex = findSeoHeaderIndex_(headers, 'updated_at');
  if (updatedAtIndex !== -1) {
    sheet.getRange(rowNumber, updatedAtIndex + 1).setValue(new Date());
  }
}

function findSeoHeaderIndex_(headers, headerName) {
  var target = String(headerName || '').toLowerCase();
  for (var i = 0; i < headers.length; i++) {
    if (String(headers[i] || '').trim().toLowerCase() === target) {
      return i;
    }
  }
  return -1;
}

function readSeoByHeader_(row, headers, headerName) {
  var index = findSeoHeaderIndex_(headers, headerName);
  return index === -1 ? '' : row[index];
}

function safeSeoErrorMessage_(error) {
  if (!error) {
    return 'Unknown error';
  }
  return String(error.message || error).substring(0, 500);
}

// ---- tiny helpers for demo polish ----

function logSeo_(message) {
  Logger.log('[SEO] ' + message);
}

/**
 * Validate that the first row of `sheet` contains exactly `expected`
 * (case-insensitive). Throws a clear error if the user reordered or renamed
 * a column — this prevents appendRow from silently writing to the wrong
 * column during the demo.
 */
function assertSeoHeaders_(sheet, expected) {
  var actual = sheet.getRange(1, 1, 1, expected.length).getValues()[0];
  for (var i = 0; i < expected.length; i++) {
    var got = String(actual[i] || '').trim().toLowerCase();
    if (got !== expected[i].toLowerCase()) {
      throw new Error(
        'Header mismatch on "' + sheet.getName() + '" col ' + (i + 1) +
        ': expected "' + expected[i] + '", got "' + got + '". ' +
        'Fix the sheet headers to match README_SEO_MVP.md schema.'
      );
    }
  }
}
