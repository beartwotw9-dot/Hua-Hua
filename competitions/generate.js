const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..');
const manifestPath=path.join(__dirname,'index.json');
const templatePath=path.join(__dirname,'_template.html');
const indexPath=path.join(root,'index.html');
const assetsRoot=path.join(__dirname,'assets');
const coversRoot=path.join(assetsRoot,'covers');
const categoryGradients={'FinTech':'linear-gradient(160deg, #2C4A3E, #6B9070)','創業競賽':'linear-gradient(160deg, #4A3020, #A0704A)','AI':'linear-gradient(160deg, #2A3A4A, #5A7A8A)','法律':'linear-gradient(160deg, #3A3020, #8A7A50)','永續':'linear-gradient(160deg, #2A4030, #5A8060)','HR':'linear-gradient(160deg, #3A2A40, #7A5A80)','培訓':'linear-gradient(160deg, #402A2A, #806040)',default:'linear-gradient(160deg, #3A3530, #8A7A6A)'};
const categoryEn={'FinTech':'FinTech','創業競賽':'Entrepreneurship','AI':'AI','法律':'Legal','永續':'Sustainability','HR':'HR','培訓':'Training','UI/UX':'UI/UX','其他':'Other'};
const awardEnMap={'作品整理':'Project Summary','作品介紹':'Project Story','團體第一名':'1st Place (Team)','全國第三':'National 3rd Place','全國季軍':'National 3rd Place','全國佳作（前10%）':'National Merit (Top 10%)','全國前六強':'National Top 6','優選':'Outstanding','全國季軍（大專組）':'National 3rd Place (College)','全國第21名':'National Rank #21','全國第22名':'National Rank #22','全國季軍（網路行銷組）':'National 3rd Place (Marketing Track)'};
const tagEnMap={'司法競賽':'Legal Challenge','醫療資料':'Medical Data','隱私保護':'Privacy Protection','HIST':'HIST','期貨':'Futures','選擇權':'Options','模擬交易':'Paper Trading','AI':'AI','ERP':'ERP','碳盤查':'Carbon Inventory','綠色金融':'Green Finance','投資模擬':'Investment Simulation','策略驗證':'Strategy Validation','風險管理':'Risk Management','UI/UX':'UI/UX','閱讀社群':'Reading Community','App設計':'App Design','AI推薦':'AI Recommendations','金融科技':'FinTech','永續信用卡':'Sustainable Credit Card','服務設計':'Service Design','科技應用':'Tech Application','跨校合作':'Cross-school Teamwork','創業構想':'Startup Concept','財務健康':'Financial Wellness','行動支付':'Mobile Payment','金融研究':'Financial Research','個股研究':'Equity Research','估值模型':'Valuation Model','財務分析':'Financial Analysis','永續':'Sustainability','模擬交易等':'Paper Trading & More','多元':'Multi-Track','新創':'Startup','商業企劃':'Business Planning','潛力':'Potential','司法':'Legal','法律思辨':'Legal Reasoning','人資':'HR','組織發展':'Org Development','職涯':'Career','青年局':'Youth Affairs','創業競賽':'Entrepreneurship','雲林':'Yunlin','地方創生':'Local Revitalization','永續園區':'Sustainable District','餐飲科技':'Food Tech','財務規劃':'Financial Planning','健康管理':'Health Management','三明治族群':'Sandwich Generation','團隊協作':'Team Collaboration','碳足跡':'Carbon Footprint','風險控管':'Risk Control','LINE':'LINE','情緒AI':'Emotional AI','數位陪伴':'Digital Companionship','社群互動':'Social Interaction','剩食媒合':'Food Rescue Matching','女性成長':'Women Growth','個人作品':'Personal Project','潛力種子盃':'Rising Seed Challenge','U-start':'U-start','創業計畫':'Startup Proposal','木育':'Wood Education','永續品牌':'Sustainable Brand','早療':'Early Intervention','AI轉譯':'AI Translation','健康科技':'Health Tech','創新創業':'Innovation & Entrepreneurship','AI應用':'AI Application','提案設計':'Proposal Design','系統思維':'Systems Thinking'};
const enProjectMap={
  '2024-少年頭家-雲林農穫':{
    title:'Yunlin Harvest Mall District',
    summary:'A startup proposal integrating local agriculture, retail, and cultural experiences into a mixed-use district near the high-speed rail area.',
    team:'Team Collaboration: Ni Nong Wo Nong Team (Ting-Yu Cui, Hsin-Hua Yu, Zhu-Xin Shi, Zhi-Yan Lai)'
  },
  '2024-新創之星-三隻雞腿排':{
    title:'What Should We Eat App',
    summary:'An AI-assisted meal decision app designed to reduce food-choice fatigue while balancing budget, nutrition, dietary constraints, and delivery.',
    team:'Team Collaboration: Three Drumsticks Team (Ting-Yu Cui, Hsin-Hua Yu, Zhi-Yan Lai)'
  },
  '2025-財務健康三明治':{
    title:'Financial Wellness Sandwich',
    summary:'A dual-track concept combining family financial planning and health management for the sandwich generation.',
    team:'Team Collaboration'
  },
  '2023-投資競賽':{
    title:'TPEx Investment Simulation',
    summary:'A simulation-based trading project focused on strategy discipline, technical and fundamental judgment, and team execution routines.',
    team:'Team Collaboration'
  },
  '2024-新創盃':{
    title:'Chaptalk Reading Community App',
    summary:'A reading community product concept connecting mood-based recommendations, social interaction, and local bookstore collaboration.',
    team:'Team Collaboration'
  },
  '2025-司法-ai個資風險':{
    title:'Legal Challenge — HIST Framework for Secondary Medical Data Use',
    summary:'A legal-tech proposal combining PETs, risk-tier governance, and regulatory framing for secondary medical data utilization.',
    team:'Team Collaboration: eeoo Team (Yu-Han Yang, Hsin-Hua Yu, and team members)'
  },
  '2025-fintech-greenpay':{
    title:'GreenPay Sustainable Credit Card',
    summary:'A FinTech concept linking daily spending behavior with measurable low-carbon incentives and ESG-aligned user engagement.',
    team:'Team Collaboration: Gui Mi Gui Mi Team'
  },
  '2025-模擬交易':{
    title:'Trade Like a Pro — Paper Trading',
    summary:'A futures and options simulation project focused on risk controls, multi-factor strategy development, and repeatable execution routines.',
    team:'Team Collaboration: Jin Tian Fa Da Cai Team'
  },
  '2025-line-pets':{
    title:'LINE PETs Emotional Companion',
    summary:'A proactive AI companion concept on LINE, combining emotional signals, daily nudges, and interaction loops for long-term engagement.',
    team:'Team Collaboration: LINE FRESH Proposal Team (Yu-Han Yang, Yi-Ting Jian, Hsin-Hua Yu)'
  },
  '2025-ai金融科技-碳感未來':{
    title:'CarbonSense Future AI Decarbonization ERP',
    summary:'An AI-enabled ERP concept for SMB carbon inventory and green-finance readiness through scenario-based decarbonization workflows.',
    team:'Team Collaboration: Three Lambs Team'
  },
  '2026-永續生活實驗室':{
    title:'ResQfood',
    summary:'A UI/UX concept for surplus-food matching that connects store-side forecasting with user-side discovery and reservation flows.',
    team:'Team Collaboration: ResQfood Team (Yu-Han Yang, Min-You Xu, Hsin-Hua Yu, Yi-Ting Jian)'
  },
  '2026-俗女手帖-app':{
    title:'Sunu Handbook App',
    summary:'A personal UI/UX project turning local lifestyle storytelling into a mobile product experience with clear user flows and interaction design.',
    team:'Individual Project'
  },
  '2025-潛力種子盃-個股研究':{
    title:'Largan Precision Equity Research',
    summary:'A stock research project covering industry context, valuation models, risk analysis, and structured investment conclusions.',
    team:'Team Collaboration: I Want to Fly Team (Jing-Xuan Pan, Wan-Shan Lu, Hsin-Hua Yu, Meng-Zhen Chen, Yu-Xin Zhang)'
  },
  '2025-拾拈溯木':{
    title:'Shi Nian Su Mu — Creative Story Construction Box',
    summary:'A startup proposal translating wood-education values into a product-service model with educational and sustainability outcomes.',
    team:'Team Collaboration: Shi Nian Su Mu Team (U-start)'
  },
  '2026-edubot':{
    title:'EduBot Early-Intervention Family Action System',
    summary:'A product concept that transforms clinical reports into daily family actions and structured follow-through for early-intervention contexts.',
    team:'Team Collaboration: EduBot Team'
  },
  '2026-系統載入中':{
    title:'System Loading (Under Maintenance)',
    summary:'This project page is temporarily under maintenance and will be updated after assets are finalized.',
    team:'Individual Curation Project'
  }
};
const i18nPage={
  zh:{brandName:'游欣樺',backText:'← 返回',overviewLabel:'作品概覽',yearLabel:'年份',categoryLabel:'類別',awardLabel:'獎項',focusLabel:'聚焦主題',storyLabel:'作品介紹',purposeLabel:'目的',ideationLabel:'發想',modelLabel:'核心設計',executionLabel:'執行方式',highlightsLabel:'內容亮點',outcomesLabel:'成果',relatedLabel:'相關作品',viewDetails:'查看詳情 →',noRelated:'尚無同分類作品。',teamOnly:'團隊協作',teamLabel:'團隊協作',appDesign:'App 設計',appScreen:'App 畫面',appCaption:'介面重點整理。'},
  en:{brandName:'Dora Yu',backText:'← Back',overviewLabel:'Overview',yearLabel:'Year',categoryLabel:'Category',awardLabel:'Award',focusLabel:'Focus',storyLabel:'Project Story',purposeLabel:'Purpose',ideationLabel:'Ideation',modelLabel:'Core Design',executionLabel:'Execution',highlightsLabel:'Highlights',outcomesLabel:'Outcomes',relatedLabel:'Related',viewDetails:'View Details →',noRelated:'No related projects yet.',teamOnly:'Team Collaboration',teamLabel:'Team Collaboration',appDesign:'App Design',appScreen:'App Screen',appCaption:'Key interface note.'}
};
function toPosix(value){return value.split(path.sep).join('/')}
function resolveSource(folder){return path.resolve(root,folder)}
function matchPatterns(file,patterns){if(!patterns||!patterns.length)return true;const haystack=(file.name+' '+file.path).toLowerCase();return patterns.some(pattern=>haystack.includes(String(pattern).toLowerCase()))}
function walk(dir,patterns){if(!fs.existsSync(dir))return[];return fs.readdirSync(dir,{withFileTypes:true}).flatMap(entry=>{const full=path.join(dir,entry.name);if(entry.isDirectory())return walk(full,patterns);if(entry.name==='.DS_Store'||entry.name==='meta.json')return[];const stat=fs.statSync(full);const rel=toPosix(path.relative(root,full));const fromPage=toPosix(path.relative(__dirname,full));const ext=path.extname(entry.name).toLowerCase();const type=['.jpg','.jpeg','.png','.webp','.gif'].includes(ext)?'image':ext==='.pdf'?'pdf':['.mp4','.mov','.m4a','.mp3'].includes(ext)?'media':ext.replace('.','')||'file';const file={name:entry.name,path:rel,url:fromPage,type,size:stat.size};return matchPatterns(file,patterns)?[file]:[]})}
function readMeta(dir){const file=path.join(dir,'meta.json');if(!fs.existsSync(file))return{};try{return JSON.parse(fs.readFileSync(file,'utf8'))}catch(error){console.warn('Could not parse '+path.relative(root,file)+': '+error.message);return{}}}
function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function inject(template,map){return template.replace(/{{(\w+)}}/g,(_,key)=>map[key]??'')}
function assetFromCompetitionPage(value){
  if(!value)return '';
  const normalized=toPosix(value);
  if(normalized.startsWith('competitions/assets/'))return normalized.slice('competitions/'.length);
  if(normalized.startsWith('assets/'))return normalized;
  return '';
}
function versionedAsset(url){
  if(!url)return '';
  const clean=url.split('?')[0];
  let target='';
  if(clean.startsWith('assets/')){
    target=path.join(__dirname,clean);
  }else if(clean.startsWith('competitions/assets/')){
    target=path.join(root,clean);
  }else{
    return clean;
  }
  if(!fs.existsSync(target))return clean;
  const stamp=Math.floor(fs.statSync(target).mtimeMs);
  return clean+'?v='+stamp;
}
function fileUrlFromRoot(value){
  return versionedAsset(assetFromCompetitionPage(value));
}
function publicCoverPath(item,coverImage){
  if(!coverImage)return null;
  const normalized=toPosix(coverImage);
  if(normalized.startsWith('competitions/assets/'))return normalized;
  if(normalized.startsWith('assets/'))return 'competitions/'+normalized;
  const source=path.resolve(root,normalized);
  if(!fs.existsSync(source))return null;
  fs.mkdirSync(coversRoot,{recursive:true});
  const ext=(path.extname(source)||'.png').toLowerCase();
  const filename=`${item.id}${ext}`;
  const target=path.join(coversRoot,filename);
  fs.copyFileSync(source,target);
  return 'competitions/assets/covers/'+filename;
}
function joinTags(item){return (item.tags||[]).join('、')}
function deriveStoryLead(item){
  if(item.storyLead)return item.storyLead;
  const teamLead=item.team?' 這個題目是團隊協作完成。':'';
  return `${item.summary}${teamLead} 這頁整理的是這個提案從問題設定、內容設計到最後成果的完整脈絡。`;
}
function toEnglishText(value){
  if(!value)return '';
  const text=String(value).trim();
  if(!text)return '';
  if(/[一-龥]/u.test(text))return '';
  return text;
}
function enTitle(item){return toEnglishText(item.titleEn||'')||toEnglishText(enProjectMap[item.id]?.title)||'Project'}
function enSummary(item){return toEnglishText(item.summaryEn||'')||toEnglishText(enProjectMap[item.id]?.summary)||'Project summary is being updated.'}
function enCategory(item){return categoryEn[item.category]||item.category||'Project'}
function enAward(item){return awardEnMap[item.award]||item.award||'Project Summary'}
function enTagValue(tag){
  const mapped=tagEnMap[tag];
  if(mapped)return mapped;
  if(/[一-龥]/u.test(String(tag||'')))return 'Project';
  return String(tag||'').trim()||'Project';
}
function enTags(item){return (item.tags||[]).map(enTagValue)}
function enSpotlights(item){
  return (item.spotlightEn||[])
    .map(toEnglishText)
    .filter(Boolean);
}
function enFocus(item){
  if(item.focusEn)return item.focusEn;
  if(item.focus){
    return String(item.focus).split('/').map(s=>s.trim()).map(enTagValue).join(' / ');
  }
  const tags=enTags(item).slice(0,2);
  return tags.join(' / ')||'Project Story';
}
function deriveStoryLeadEn(item){
  if(item.storyLeadEn)return item.storyLeadEn;
  const parts=[enSummary(item)];
  if(item.team)parts.push('This project was completed through team collaboration.');
  parts.push('This page outlines the project from problem framing to final deliverables.');
  return parts.join(' ');
}
function derivePurposeEn(item){
  if(item.purposeEn)return item.purposeEn;
  if(item.category==='UI/UX')return 'This project was built to turn an idea into a clearer interface concept and user flow.';
  if(item.category==='FinTech')return 'This project was built to turn a finance-related topic into a clearer product or service concept.';
  if(item.category==='創業競賽')return 'This project was built to turn a problem into a more complete startup proposal.';
  if(item.category==='AI')return 'This project was built to place AI inside a more concrete use case instead of describing the technology in the abstract.';
  if(item.category==='法律')return 'This project was built to organize a legal and technology issue into a clearer argument and proposal.';
  return 'This project was built to organize the topic into a clear and presentable piece of work.';
}
function deriveIdeationEn(item){
  if(item.ideationEn)return item.ideationEn;
  return 'The idea started from the topic itself and was refined by clarifying the user context, the main problem, and what the final deliverable needed to communicate.';
}
function deriveModelEn(item){
  if(item.modelEn)return item.modelEn;
  const spots=enSpotlights(item).slice(0,3);
  if(spots.length)return `The content was mainly organized around ${spots.join(', ')}.`;
  return 'The content was organized into a simple structure that made the project easier to explain and review.';
}
function deriveExecutionEn(item){
  if(item.executionEn)return item.executionEn;
  const spots=enSpotlights(item).slice(0,3);
  if(item.appScreens&&item.appScreens.length)return 'The work focused on organizing screens, user flow, and interface decisions into one consistent app concept.';
  if(spots.length)return `The final deliverable was organized around ${spots.join(', ')}.`;
  return 'The work focused on organizing the materials into a version that could be clearly presented.';
}
function deriveHighlightsEn(item){
  if(item.highlightsEn)return item.highlightsEn;
  const spots=enSpotlights(item).slice(0,3);
  if(spots.length)return `Key points included ${spots.join(', ')}.`;
  const tags=enTags(item).slice(0,3);
  if(tags.length)return `Key points included ${tags.join(', ')}.`;
  return 'The main value of the project was a clear structure and a focused presentation.';
}
function deriveOutcomesEn(item){
  if(item.outcomesEn)return item.outcomesEn;
  const award=enAward(item);
  if(item.award&&award!=='Project Summary')return `Outcome: ${award}.`;
  if(item.appScreens&&item.appScreens.length)return 'Outcome: a finished app concept with selected screens and a presentable flow.';
  if(item.category==='FinTech')return 'Outcome: a finished finance-related proposal or analysis piece.';
  if(item.category==='創業競賽')return 'Outcome: a finished startup proposal.';
  return 'Outcome: a finished and presentable project deliverable.';
}
function derivePurpose(item){
  if(item.purpose)return item.purpose;
  if(item.category==='UI/UX')return '這個作品主要是在整理介面概念、使用流程和整體產品方向。';
  if(item.category==='FinTech')return '這個作品主要是在整理金融主題，並把它轉成比較清楚的產品或研究提案。';
  if(item.category==='創業競賽')return '這個作品主要是在把問題整理成一份比較完整的創業提案。';
  if(item.category==='AI')return '這個作品主要是在思考 AI 可以放進什麼情境，並把它整理成可說明的應用概念。';
  if(item.category==='法律')return '這個作品主要是在把法律與科技議題整理成較清楚的論述與提案。';
  return '這個作品主要是在把題目整理成一份清楚、可展示的成果。';
}
function deriveIdeation(item){
  if(item.ideation)return item.ideation;
  return '發想是從題目本身的情境和問題意識出發，再往下整理內容範圍、使用對象和最後要呈現的重點。';
}
function deriveModel(item){
  if(item.model)return item.model;
  const spots=(item.spotlight||[]).slice(0,3);
  if(spots.length)return `內容主要圍繞 ${spots.join('、')} 這幾個部分展開。`;
  return '內容以主題說明、重點整理和成果呈現為主。';
}
function deriveOutcomes(item){
  if(item.outcomes)return item.outcomes;
  if(item.award)return `成果是獲得「${item.award}」。`;
  if(item.appScreens&&item.appScreens.length)return '成果是一套可展示的 App 概念與畫面整理。';
  if(item.category==='FinTech')return '成果是一份完成度較高的金融提案或研究整理。';
  if(item.category==='創業競賽')return '成果是一份完整的創業提案。';
  return '成果是一份可以公開展示的完成版本。';
}
function deriveExecution(item){
  if(item.execution)return item.execution;
  const spots=(item.spotlight||[]).slice(0,3);
  if(item.appScreens&&item.appScreens.length)return '這個作品主要是把畫面、功能想法和使用流程整理成一個可閱讀的 App 提案。';
  if(spots.length)return `執行上，我把內容整理到 ${spots.join('、')} 這幾個重點裡。`;
  return '執行上，主要是把資料、內容和展示方式整理成一個比較完整的版本。';
}
function deriveHighlights(item){
  if(item.highlights)return item.highlights;
  const points=(item.spotlight||[]).slice(0,3);
  if(points.length)return `這份作品主要看的就是 ${points.join('、')}。`;
  const tags=(item.tags||[]).slice(0,3);
  if(tags.length)return `這份作品主要聚焦在 ${tags.join('、')}。`;
  return '這份作品的重點是把題目整理得清楚。';
}
function renderStoryShowcase(item,spotlightHtml,lang='zh'){
  const t=i18nPage[lang];
  const noteTitle=lang==='zh'?'內容補充':'Additional Notes';
  const noteBody=lang==='zh'
    ?(item.reflection||deriveExecution(item))
    :((item.reflectionEn&&toEnglishText(item.reflectionEn))||deriveExecutionEn(item));
  const imageCaption=lang==='zh'?'精選作品畫面與主視覺整理。':'Selected visuals and key interface snapshots.';
  const localizedTitle=lang==='en'?enTitle(item):item.title;
  const imageBlock=item.coverImage?`<div class="story-media"><img src="${esc(fileUrlFromRoot(item.coverImage))}" alt="${esc(localizedTitle)} ${t.appScreen}"/><p class="story-media-caption">${imageCaption}</p></div>`:'';
  const note=`<article class="story-note"><h3>${noteTitle}</h3><p>${esc(noteBody)}</p>${spotlightHtml?`<ul class="spotlight-list">${spotlightHtml}</ul>`:''}</article>`;
  if(!imageBlock&&!spotlightHtml)return '';
  return `<div class="story-showcase">${imageBlock||''}${note}</div>`;
}
function deriveFocus(item){
  return item.focus || (item.spotlight&&item.spotlight.length ? item.spotlight.slice(0,2).join(' / ') : (item.tags||[]).slice(0,2).join(' / ') || '作品介紹');
}
function renderAppGallery(item,lang='zh'){
  const t=i18nPage[lang];
  if(!item.appScreens||!item.appScreens.length)return '';
  const localizedTitle=lang==='en'?enTitle(item):item.title;
  const introText=lang==='en'
    ?(item.appIntroEn||'This section presents key app flows and interaction decisions through selected mobile screens.')
    :(item.appIntro||'');
  const intro=introText?'<div class="app-intro-block"><p class="app-intro">'+esc(introText)+'</p></div>':'';
  const cards=item.appScreens.map((screen,index)=>{
    const imagePath=versionedAsset(assetFromCompetitionPage(screen.image||''));
    const screenTitle=lang==='en'
      ?(screen.titleEn||`${t.appScreen} ${index+1}`)
      :(screen.title||`${t.appScreen} ${index+1}`);
    const screenCaption=lang==='en'
      ?(screen.captionEn||t.appCaption)
      :(screen.caption||t.appCaption);
    const image=imagePath
      ?'<img src="'+esc(imagePath)+'" alt="'+esc(localizedTitle)+' - '+esc(screenTitle)+'" loading="'+(index<2?'eager':'lazy')+'" decoding="async"/>'
      :'<div class="app-shot-fallback"><strong>'+esc(screenTitle)+'</strong></div>';
    return '<article class="app-slide"><div class="app-phone"><span class="app-notch"></span><div class="app-shot">'+image+'</div></div><div class="app-slide-body"><h3>'+esc(screenTitle)+'</h3><p>'+esc(screenCaption)+'</p></div></article>';
  }).join('');
  const prevAria=lang==='zh'?'上一張':'Previous';
  const nextAria=lang==='zh'?'下一張':'Next';
  const dotsAria=lang==='zh'?'App 畫面切換':'App gallery dots';
  return '<section><div class="wrap"><h2 class="section-title">'+t.appDesign+'</h2>'+intro+'<div class="app-carousel" data-app-carousel><button class="app-nav-btn prev" type="button" aria-label="'+prevAria+'">‹</button><div class="app-track" tabindex="0">'+cards+'</div><button class="app-nav-btn next" type="button" aria-label="'+nextAria+'">›</button></div><div class="app-dots" aria-label="'+dotsAria+'"></div></div></section>';
}
function renderTeamBlock(item,lang='zh'){
  const t=i18nPage[lang];
  if(!item.team)return '';
  const rawTeam=lang==='en'
    ?(item.teamEn||enProjectMap[item.id]?.team||'')
    :item.team;
  const teamText=String(rawTeam).trim();
  if(!teamText)return '';
  if(/^(團體協作|團隊協作|Team Collaboration)$/u.test(teamText)){
    return '<div class="team-note"><strong>'+t.teamOnly+'</strong></div>';
  }
  if(lang==='en'&&/^Team Collaboration\s*:/i.test(teamText)){
    return '<div class="team-note"><strong>'+esc(teamText)+'</strong></div>';
  }
  const sep=lang==='en'?':':'：';
  return '<div class="team-note"><strong>'+t.teamLabel+'</strong>'+sep+' '+esc(teamText)+'</div>';
}
function updateIndex(data){if(!fs.existsSync(indexPath))return;let html=fs.readFileSync(indexPath,'utf8');const light=data.map(({files,filePatterns,spotlight,reflection,purpose,ideation,model,outcomes,focus,appIntro,appScreens,appVisual,...item})=>item);html=html.replace(/const competitionData=\[[\s\S]*?\];\nconst carouselRoot=/,'const competitionData='+JSON.stringify(light)+';\nconst carouselRoot=');fs.writeFileSync(indexPath,html,'utf8')}
const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
const template=fs.readFileSync(templatePath,'utf8');
manifest.competitions=manifest.competitions.map(item=>{
  const folder=resolveSource(item.sourceFolder);
  const meta=readMeta(folder);
  const merged={...item,...meta};
  const files=walk(folder,merged.filePatterns);
  const firstImage=files.find(file=>file.type==='image');
  const rawCover=merged.coverImage??firstImage?.path??null;
  const coverImage=publicCoverPath(merged,rawCover);
  return {...merged,coverImage,files};
});
fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2),'utf8');
const expected=new Set(manifest.competitions.flatMap(item=>[item.id+'.html',item.id+'-en.html']));
for(const file of fs.readdirSync(__dirname)){if(file.endsWith('.html')&&file!=='_template.html'&&!expected.has(file))fs.unlinkSync(path.join(__dirname,file))}
function renderPage(item,lang='zh'){
  const t=i18nPage[lang];
  const isZh=lang==='zh';
  const related=manifest.competitions
    .filter(other=>other.id!==item.id&&other.category===item.category)
    .slice(0,3)
    .map(other=>{
      const href=encodeURIComponent(other.id)+(isZh?'.html':'-en.html');
      const title=isZh?other.title:enTitle(other);
      const category=isZh?other.category:enCategory(other);
      return '<a class="related-card" href="'+href+'"><div class="related-year">'+esc(other.year)+' · '+esc(category)+'</div><div class="related-title">'+esc(title)+'</div><span class="small-link">'+t.viewDetails+'</span></a>';
    }).join('')||'<div class="note">'+t.noRelated+'</div>';
  const tags=(isZh?item.tags:enTags(item)).map(tag=>'<span class="pill">'+esc(tag)+'</span>').join('');
  const spotlightSource=isZh
    ?(item.spotlight||[])
    :(enSpotlights(item).length?enSpotlights(item):(item.spotlight||[]).map(enTagValue));
  const spotlightItems=spotlightSource.map(point=>'<li>'+esc(point)+'</li>').join('');
  const category=isZh?item.category:enCategory(item);
  const title=isZh?item.title:enTitle(item);
  const summary=isZh?item.summary:enSummary(item);
  const awardText=isZh?(item.award||'作品整理'):enAward(item);
  const focus=isZh?deriveFocus(item):enFocus(item);
  const storyLead=isZh?deriveStoryLead(item):deriveStoryLeadEn(item);
  const purpose=isZh?derivePurpose(item):derivePurposeEn(item);
  const ideation=isZh?deriveIdeation(item):deriveIdeationEn(item);
  const model=isZh?deriveModel(item):deriveModelEn(item);
  const execution=isZh?deriveExecution(item):deriveExecutionEn(item);
  const highlights=isZh?deriveHighlights(item):deriveHighlightsEn(item);
  const outcomes=isZh?deriveOutcomes(item):deriveOutcomesEn(item);
  const html=inject(template,{
    langAttr:isZh?'zh-Hant':'en',
    pageTitleSuffix:isZh?'競賽作品集':'Project Archive',
    brandName:t.brandName,
    backText:t.backText,
    overviewLabel:t.overviewLabel,
    yearLabel:t.yearLabel,
    categoryLabel:t.categoryLabel,
    awardLabel:t.awardLabel,
    focusLabel:t.focusLabel,
    storyLabel:t.storyLabel,
    purposeLabel:t.purposeLabel,
    ideationLabel:t.ideationLabel,
    modelLabel:t.modelLabel,
    executionLabel:t.executionLabel,
    highlightsLabel:t.highlightsLabel,
    outcomesLabel:t.outcomesLabel,
    relatedLabel:t.relatedLabel,
    goToSlideLabel:isZh?'切換到第 ':'Go to slide ',
    goToSlideSuffix:isZh?' 張':'',
    title:esc(title),
    year:esc(item.year),
    category:esc(category),
    summary:esc(summary),
    awardText:esc(awardText),
    focus:esc(focus),
    storyLead:esc(storyLead),
    purpose:esc(purpose),
    ideation:esc(ideation),
    model:esc(model),
    execution:esc(execution),
    highlights:esc(highlights),
    outcomes:esc(outcomes),
    storyShowcase:renderStoryShowcase(item,spotlightItems,lang),
    teamBlock:renderTeamBlock(item,lang),
    gradient:categoryGradients[item.category]||categoryGradients.default,
    awardBadge:'<span class="pill award">'+esc(awardText)+'</span>',
    tags,
    spotlight:spotlightItems?'<ul class="spotlight-list">'+spotlightItems+'</ul>':'',
    appGallery:renderAppGallery(item,lang),
    related,
    json:JSON.stringify({lang}).replace(/<\//g,'<\\/')
  });
  const fileName=item.id+(isZh?'.html':'-en.html');
  fs.writeFileSync(path.join(__dirname,fileName),html,'utf8');
}
for(const item of manifest.competitions){
  renderPage(item,'zh');
  renderPage(item,'en');
}
updateIndex(manifest.competitions);
const awarded=manifest.competitions.filter(item=>item.awarded).length;
const fileCount=manifest.competitions.reduce((sum,item)=>sum+item.files.length,0);
console.log('Generated '+manifest.competitions.length+' pages. '+awarded+' with awards, '+(manifest.competitions.length-awarded)+' without. '+fileCount+' files indexed.');
