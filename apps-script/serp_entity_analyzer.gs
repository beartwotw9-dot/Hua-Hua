/**
 * SERP Entity Analyzer — SEO Automation MVP (1-day build)
 * --------------------------------------------------------
 * Reads the first `pending` row from `SERP_Input`, pulls (or mocks)
 * Google's top-10 organic results, runs a tiny on-script entity heuristic,
 * and appends 10 rows to `SERP_Results`. Then marks the input row `done`.
 *
 * Demo mode:
 *   SERP_MOCK_MODE = true   -> no API key needed, returns canned results
 *   SERP_MOCK_MODE = false  -> hits SerpAPI / ValueSERP via Script Properties
 *
 * Why a heuristic instead of real NLP?
 *   This is a 1-day MVP. Chinese bigrams + English tokens + stopword filter
 *   is intentionally crude — it proves the wiring, not the linguistics.
 *   See README_SEO_MVP.md "MVP tradeoffs".
 *
 * TODO (post-MVP, intentionally NOT in scope for this demo):
 *   - Replace heuristic with Cloud NL API or LLM embeddings
 *   - Batch process more than one pending row per run
 *   - Dedupe on (keyword, link) so reruns don't pile up rows
 */

// ---- Config (script-global; intentionally namespaced with SERP_ to avoid
//      collision with seo_content_generator.gs in the same project) ----
var SERP_MOCK_MODE = true;
var SERP_INPUT_SHEET = 'SERP_Input';
var SERP_RESULTS_SHEET = 'SERP_Results';

// Header order for SERP_Results. appendRow writes positionally, so this
// must match the sheet exactly. Validated at runtime by assertSerpHeaders_.
var SERP_RESULTS_HEADERS = [
  'timestamp', 'input_row', 'keyword', 'position', 'title',
  'link', 'snippet', 'top_entities', 'token_count', 'mode'
];

function analyzeSerpEntities() {
  var mode = SERP_MOCK_MODE ? 'MOCK' : 'LIVE';
  logSerp_('--- analyzeSerpEntities start (mode=' + mode + ') ---');

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var inputSheet = getRequiredSerpSheet_(ss, SERP_INPUT_SHEET);
  var resultsSheet = getRequiredSerpSheet_(ss, SERP_RESULTS_SHEET);
  assertSerpHeaders_(resultsSheet, SERP_RESULTS_HEADERS);

  var pending = findFirstPendingSerpRow_(inputSheet);
  if (!pending) {
    logSerp_('No pending SERP rows. Nothing to do.');
    return;
  }

  var rowNumber = pending.rowNumber;
  var row = pending.row;
  var headers = pending.headers;
  var keyword = String(readSerpByHeader_(row, headers, 'keyword') || '').trim();

  if (!keyword) {
    markSerpStatus_(inputSheet, headers, rowNumber, 'error', 'Missing keyword');
    throw new Error('SERP_Input row ' + rowNumber + ' is missing keyword.');
  }
  logSerp_('Picked row ' + rowNumber + ' keyword="' + keyword + '"');

  try {
    markSerpStatus_(inputSheet, headers, rowNumber, 'processing', '');
    var organicResults = SERP_MOCK_MODE ? getMockSerpResults_(keyword) : fetchSerpResults_(keyword);
    var top10 = organicResults.slice(0, 10);
    if (!top10.length) {
      throw new Error('No organic results returned.');
    }

    top10.forEach(function(result, index) {
      var entitySummary = summarizeEntities_(keyword, result.title, result.snippet);
      resultsSheet.appendRow([
        new Date(),
        rowNumber,
        keyword,
        index + 1,
        result.title || '',
        result.link || '',
        result.snippet || '',
        entitySummary.topEntities.join(', '),
        entitySummary.tokenCount,
        SERP_MOCK_MODE ? 'mock' : 'live'
      ]);
    });

    markSerpStatus_(inputSheet, headers, rowNumber, 'done', '');
    logSerp_('Wrote ' + top10.length + ' rows to ' + SERP_RESULTS_SHEET +
             ', marked input row ' + rowNumber + ' as done.');
  } catch (error) {
    markSerpStatus_(inputSheet, headers, rowNumber, 'error', safeSerpErrorMessage_(error));
    logSerp_('ERROR on row ' + rowNumber + ': ' + safeSerpErrorMessage_(error));
    throw error;
  }
}

function fetchSerpResults_(keyword) {
  var props = PropertiesService.getScriptProperties();
  var provider = String(props.getProperty('SERP_PROVIDER') || 'serpapi').toLowerCase();
  var apiKey = String(props.getProperty('SERP_API_KEY') || '').trim();
  if (!apiKey) {
      throw new Error('SERP_API_KEY is required when SERP_MOCK_MODE=false.');
  }

  var response;
  if (provider === 'valueserp') {
    var valueSerpUrl =
      'https://api.valueserp.com/search?api_key=' + encodeURIComponent(apiKey) +
      '&q=' + encodeURIComponent(keyword) +
      '&location=' + encodeURIComponent(props.getProperty('SERP_LOCATION') || 'Taiwan') +
      '&hl=' + encodeURIComponent(props.getProperty('SERP_LANGUAGE') || 'zh-tw') +
      '&gl=' + encodeURIComponent(props.getProperty('SERP_COUNTRY') || 'tw') +
      '&num=10';
    response = UrlFetchApp.fetch(valueSerpUrl, { muteHttpExceptions: true });
  } else {
    var serpApiUrl =
      'https://serpapi.com/search.json?engine=google' +
      '&api_key=' + encodeURIComponent(apiKey) +
      '&q=' + encodeURIComponent(keyword) +
      '&location=' + encodeURIComponent(props.getProperty('SERP_LOCATION') || 'Taiwan') +
      '&hl=' + encodeURIComponent(props.getProperty('SERP_LANGUAGE') || 'zh-tw') +
      '&gl=' + encodeURIComponent(props.getProperty('SERP_COUNTRY') || 'tw') +
      '&num=10';
    response = UrlFetchApp.fetch(serpApiUrl, { muteHttpExceptions: true });
  }

  if (response.getResponseCode() >= 300) {
    throw new Error('SERP API error ' + response.getResponseCode() + ': ' + response.getContentText());
  }

  var json = JSON.parse(response.getContentText());
  var organic = json.organic_results || [];
  return organic.map(function(item) {
    return {
      title: item.title || '',
      link: item.link || item.url || '',
      snippet: item.snippet || item.description || ''
    };
  });
}

function getMockSerpResults_(keyword) {
  var templates = [
    ['2026 ' + keyword + '完整指南：品牌、成分與預算整理', 'https://example.com/guide', keyword + '常見品牌、成分差異、價格帶與新手選購重點總整理。'],
    [keyword + '推薦排行榜，獸醫與飼主最常提到的 10 個重點', 'https://example.com/ranking', '從蛋白質比例、穀物配方、腸胃敏感與適口性分析' + keyword + '選擇。'],
    ['小型犬適合的' + keyword + '怎麼挑？', 'https://example.com/small-dog', '整理室內小型犬、體重管理、毛髮保健與高齡犬需求。'],
    [keyword + '成分表怎麼看：蛋白質、脂肪、纖維一次懂', 'https://example.com/ingredients', '學會閱讀成分與營養標示，避開不必要添加物與行銷話術。'],
    ['平價與高階' + keyword + '差在哪？', 'https://example.com/price', '比較價格、原料來源、製程與保存方式，幫助控制每月飼料預算。'],
    ['敏感腸胃也能吃的' + keyword + '清單', 'https://example.com/sensitive', '聚焦低敏配方、單一蛋白、無穀與益生菌補充觀點。'],
    ['熱門品牌' + keyword + '實測心得', 'https://example.com/review', '彙整飼主回饋、便便狀況、毛色變化與嗜口性觀察。'],
    ['幼犬、成犬、高齡犬的' + keyword + '差異', 'https://example.com/life-stage', '從年齡、活動量與牙口變化拆解不同生命階段需求。'],
    ['保存' + keyword + '的 7 個細節', 'https://example.com/storage', '包含開封後保存、分裝、受潮風險與最佳食用期限。'],
    [keyword + '常見 QA：換糧、份量與搭配罐頭', 'https://example.com/qa', '回答換糧週期、餵食份量、混搭零食與常見誤區。']
  ];

  return templates.map(function(item) {
    return { title: item[0], link: item[1], snippet: item[2] };
  });
}

function summarizeEntities_(keyword, title, snippet) {
  var text = [keyword, title, snippet].join(' ');
  var counts = {};
  extractChineseBigrams_(text).forEach(function(token) {
    if (!isSerpStopword_(token)) {
      counts[token] = (counts[token] || 0) + 1;
    }
  });
  extractEnglishTokens_(text).forEach(function(token) {
    if (!isSerpStopword_(token)) {
      counts[token] = (counts[token] || 0) + 1;
    }
  });

  var topEntities = Object.keys(counts)
    .sort(function(a, b) { return counts[b] - counts[a] || a.localeCompare(b); })
    .slice(0, 5)
    .map(function(token) { return token + ' (' + counts[token] + ')'; });

  return {
    topEntities: topEntities,
    tokenCount: Object.keys(counts).length
  };
}

function extractChineseBigrams_(text) {
  var matches = String(text || '').match(/[\u4e00-\u9fff]{2,}/g) || [];
  var output = [];
  matches.forEach(function(chunk) {
    for (var i = 0; i < chunk.length - 1; i++) {
      output.push(chunk.substring(i, i + 2));
    }
  });
  return output;
}

function extractEnglishTokens_(text) {
  var matches = String(text || '').toLowerCase().match(/[a-z0-9][a-z0-9\-]{1,}/g) || [];
  return matches;
}

function isSerpStopword_(token) {
  var stopwords = {
    '的': true, '了': true, '和': true, '是': true, '在': true, '與': true, '及': true, '用': true,
    '推薦': true, '怎麼': true, '如何': true, '整理': true, '完整': true, '指南': true, '常見': true,
    'and': true, 'the': true, 'for': true, 'with': true, 'that': true, 'this': true, 'from': true,
    'into': true, 'your': true, 'best': true, 'guide': true, '2026': true
  };
  return !token || stopwords[token] || token.length < 2;
}

function getRequiredSerpSheet_(ss, sheetName) {
  var sheet = ss.getSheetByName(sheetName);
  if (!sheet) {
    throw new Error('Missing sheet: ' + sheetName);
  }
  return sheet;
}

function findFirstPendingSerpRow_(sheet) {
  var values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    return null;
  }
  var headers = values[0];
  var statusIndex = findSerpHeaderIndex_(headers, 'status');
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

function markSerpStatus_(sheet, headers, rowNumber, status, errorMessage) {
  var statusIndex = findSerpHeaderIndex_(headers, 'status');
  if (statusIndex !== -1) {
    sheet.getRange(rowNumber, statusIndex + 1).setValue(status);
  }
  var errorIndex = findSerpHeaderIndex_(headers, 'error_message');
  if (errorIndex !== -1) {
    sheet.getRange(rowNumber, errorIndex + 1).setValue(errorMessage || '');
  }
  var updatedAtIndex = findSerpHeaderIndex_(headers, 'updated_at');
  if (updatedAtIndex !== -1) {
    sheet.getRange(rowNumber, updatedAtIndex + 1).setValue(new Date());
  }
}

function findSerpHeaderIndex_(headers, headerName) {
  var target = String(headerName || '').toLowerCase();
  for (var i = 0; i < headers.length; i++) {
    if (String(headers[i] || '').trim().toLowerCase() === target) {
      return i;
    }
  }
  return -1;
}

function readSerpByHeader_(row, headers, headerName) {
  var index = findSerpHeaderIndex_(headers, headerName);
  return index === -1 ? '' : row[index];
}

function safeSerpErrorMessage_(error) {
  if (!error) {
    return 'Unknown error';
  }
  return String(error.message || error).substring(0, 500);
}

// ---- tiny helpers for demo polish ----

function logSerp_(message) {
  Logger.log('[SERP] ' + message);
}

/**
 * Validate that the first row of `sheet` contains exactly `expected`
 * (case-insensitive). Throws a clear error if the user reordered or renamed
 * a column — this prevents appendRow from silently writing to the wrong
 * column during the demo.
 */
function assertSerpHeaders_(sheet, expected) {
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
