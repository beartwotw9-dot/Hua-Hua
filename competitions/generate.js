const fs=require('fs');
const path=require('path');
const root=path.resolve(__dirname,'..');
const manifestPath=path.join(__dirname,'index.json');
const templatePath=path.join(__dirname,'_template.html');
const indexPath=path.join(root,'index.html');
const assetsRoot=path.join(__dirname,'assets');
const coversRoot=path.join(assetsRoot,'covers');
const categoryGradients={'FinTech':'linear-gradient(160deg, #2C4A3E, #6B9070)','創業競賽':'linear-gradient(160deg, #4A3020, #A0704A)','AI':'linear-gradient(160deg, #2A3A4A, #5A7A8A)','法律':'linear-gradient(160deg, #3A3020, #8A7A50)','永續':'linear-gradient(160deg, #2A4030, #5A8060)','HR':'linear-gradient(160deg, #3A2A40, #7A5A80)','培訓':'linear-gradient(160deg, #402A2A, #806040)',default:'linear-gradient(160deg, #3A3530, #8A7A6A)'};
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
  const teamLead=item.team?'這個題目是團隊協作完成。':'';
  return item.storyLead || `${item.summary} ${teamLead}這頁整理的是我在這個題目裡如何定義問題、形成提案、安排內容架構，以及最後想讓外部看見的核心價值。`;
}
function derivePurpose(item){
  if(item.purpose)return item.purpose;
  const tags=joinTags(item);
  if(item.category==='FinTech')return `這個作品希望把 ${tags||'金融服務'} 轉化成更容易被理解與採用的方案，讓評審能看見我如何用財金背景處理真實市場問題。`;
  if(item.category==='創業競賽')return `這個作品的目標是把一個具體痛點整理成可落地的創業提案，驗證市場需求、使用情境與執行可行性。`;
  if(item.category==='AI')return `這個提案希望把 AI 技術放進真實應用場景，讓創新不只停留在工具展示，而是能回應明確問題與使用需求。`;
  if(item.category==='法律')return `這個作品聚焦在制度、風險與人之間的關係，目標是用清楚論述呈現我對科技與法律議題的理解。`;
  if(item.category==='永續')return `這個作品希望從生活與社會議題出發，提出兼顧價值與可執行性的永續方案。`;
  if(item.category==='HR')return `這段經歷的核心目標是把人資知識轉成可應用的實務能力，建立我對組織、人才與制度的理解。`;
  return `這個作品是我把 ${tags||'跨域學習'} 轉化成具體成果的整理，讓外部能快速理解主題、做法與產出。`;
}
function deriveIdeation(item){
  if(item.ideation)return item.ideation;
  if(item.category==='FinTech')return `發想來自我對金融產品使用情境的觀察：如果金融服務要真正被採用，除了專業性，還需要更清楚的使用者價值與體驗設計。`;
  if(item.category==='創業競賽')return `發想起點通常是生活痛點、在地需求或特定族群的未被滿足需求，再往下延伸成完整提案。`;
  if(item.category==='AI')return `發想從「AI 可以做什麼」轉成「哪個問題值得被解」，因此我先定義場景，再選擇技術如何介入。`;
  if(item.category==='法律')return `發想來自我對科技發展與制度風險的好奇，想釐清當新技術進入日常後，個人權益如何被保護。`;
  if(item.category==='永續')return `發想從永續議題與日常行為落差出發，思考怎麼把抽象價值轉成可被理解與參與的方案。`;
  if(item.category==='HR')return `發想來自我對人的決策與組織運作的興趣，因此不只記錄課程內容，也整理自己如何把知識轉成工作方法。`;
  return `發想來自學習過程中的關鍵問題與實作經驗，整理出一個能代表我思考方式的作品版本。`;
}
function deriveModel(item){
  if(item.model)return item.model;
  const spots=(item.spotlight||[]).join('、');
  if(item.category==='創業競賽')return `我把商業模式拆成目標客群、價值主張、執行流程與資源配置四個面向，再用 ${spots||'企劃與簡報'} 讓整體結構更完整。`;
  if(item.category==='FinTech')return `核心設計聚焦在產品定位、使用流程、獲客邏輯與價值交換，並用 ${spots||'企劃書與分析資料'} 支撐提案說服力。`;
  if(item.category==='AI')return `核心設計以問題場景、技術角色、資料流與使用者體驗為主軸，確保 AI 是解法的一部分，而不是附加亮點。`;
  if(item.category==='永續')return `我用問題定義、利害關係人、行動方案與可持續運作方式來組織提案，讓概念可以被實際執行。`;
  if(item.category==='HR')return `我把內容整理成知識模組、實務觀察與可應用方法三層，讓學習不只是吸收，而是能被帶回工作現場。`;
  return `這個作品的核心設計是把主題拆成易理解的架構，再用文字、分析與展示素材把重點收束成完整敘事。`;
}
function deriveOutcomes(item){
  if(item.outcomes)return item.outcomes;
  if(item.award)return `成果上，這個作品獲得「${item.award}」，也讓我更清楚如何把複雜主題整理成能被評審與外部理解的提案。`;
  if(item.category==='培訓'||item.category==='HR')return `成果不只是在課程或資料上的累積，更重要的是我建立了後續可以持續使用的知識框架與實務判斷方式。`;
  return `成果上，這個作品讓我完成從研究、整理到表達的一整段流程，也成為我後續做提案與跨域整合的重要基礎。`;
}
function deriveExecution(item){
  if(item.execution)return item.execution;
  const tags=joinTags(item);
  if(item.category==='FinTech')return `執行上，我先整理市場脈絡與使用情境，再把分析、提案與展示內容收束成同一條敘事線，讓 ${tags||'金融主題'} 不只停留在研究，而能被看成一個具體產品或服務構想。`;
  if(item.category==='創業競賽')return `我會先拆出問題、目標客群與價值主張，再往下整理商業模式、體驗流程與提案結構，讓想法從概念走到一個比較完整、可以被說服的創業方案。`;
  if(item.category==='AI')return `執行方式上，我先定義使用場景與需求，再決定 AI 要扮演什麼角色，並把技術、流程與價值說明整理成同一個提案結構。`;
  if(item.category==='法律')return `我以議題脈絡、風險辨識與論點整理為主軸，讓內容不只停留在抽象立場，而是能一步一步說清楚制度與實務上的差異。`;
  if(item.category==='永續')return `執行上，我把問題拆成使用者端、供給端與平台端三個視角，同時整理行為誘因、運作流程與價值衡量方式，讓永續主題更接近實際產品提案。`;
  if(item.category==='HR'||item.category==='培訓')return `我會先整理知識架構與實作脈絡，再回頭拆出哪些內容可以真正落地到工作場景，讓這份整理不只是紀錄，而是可延伸使用的方法。`;
  return `執行上，我先整理資料與主題架構，再挑出最能代表作品價值的內容重點，重新組織成比較完整、也比較容易閱讀的作品版本。`;
}
function deriveHighlights(item){
  if(item.highlights)return item.highlights;
  const points=(item.spotlight||[]).slice(0,3);
  if(points.length)return `這份作品的內容亮點主要集中在 ${points.join('、')}，也因此能更完整地呈現我如何從主題理解一路走到企劃、設計或提案表達。`;
  const tags=(item.tags||[]).slice(0,3);
  if(tags.length)return `這份作品特別聚焦在 ${tags.join('、')} 等幾個面向，讓主題不只被描述，而是能被具體拆解、展示與延伸。`;
  return `這份作品的亮點在於把原本分散的想法與資料整理成有重點的展示內容，讓外部能更快理解我處理這個題目的方式。`;
}
function renderStoryShowcase(item,spotlightHtml){
  const image=item.coverImage?`<div class="story-media"><img src="${esc(fileUrlFromRoot(item.coverImage))}" alt="${esc(item.title)} 作品畫面"/><p class="story-media-caption">精選作品畫面與主視覺整理。</p></div>`:'';
  const note=`<article class="story-note"><h3>內容補充</h3><p>${esc(deriveExecution(item))}</p>${spotlightHtml?`<ul class="spotlight-list">${spotlightHtml}</ul>`:''}</article>`;
  if(!image&&!spotlightHtml)return '';
  return `<div class="story-showcase">${image||''}${note}</div>`;
}
function deriveFocus(item){
  return item.focus || (item.spotlight&&item.spotlight.length ? item.spotlight.slice(0,2).join(' / ') : (item.tags||[]).slice(0,2).join(' / ') || '作品介紹');
}
function renderAppGallery(item){
  if(!item.appScreens||!item.appScreens.length)return '';
  const intro=item.appIntro?'<div class="app-intro-block"><p class="app-intro">'+esc(item.appIntro)+'</p></div>':'';
  const cards=item.appScreens.map((screen,index)=>{
    const imagePath=versionedAsset(assetFromCompetitionPage(screen.image||''));
    const image=imagePath
      ?'<img src="'+esc(imagePath)+'" alt="'+esc(item.title)+' - '+esc(screen.title)+' 畫面" loading="'+(index<2?'eager':'lazy')+'" decoding="async"/>'
      :'<div class="app-shot-fallback"><strong>'+esc(screen.title||'App 畫面')+'</strong></div>';
    return '<article class="app-slide"><div class="app-phone"><span class="app-notch"></span><div class="app-shot">'+image+'</div></div><div class="app-slide-body"><h3>'+esc(screen.title||'介面重點')+'</h3><p>'+esc(screen.caption||'介面重點整理。')+'</p></div></article>';
  }).join('');
  return '<section><div class="wrap"><h2 class="section-title">App 設計</h2>'+intro+'<div class="app-carousel" data-app-carousel><button class="app-nav-btn prev" type="button" aria-label="上一張">‹</button><div class="app-track" tabindex="0">'+cards+'</div><button class="app-nav-btn next" type="button" aria-label="下一張">›</button></div><div class="app-dots" aria-label="App 畫面切換"></div></div></section>';
}
function renderTeamBlock(item){
  if(!item.team)return '';
  return '<div class="team-note"><strong>團隊協作</strong></div>';
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
const expected=new Set(manifest.competitions.map(item=>item.id+'.html'));
for(const file of fs.readdirSync(__dirname)){if(file.endsWith('.html')&&file!=='_template.html'&&!expected.has(file))fs.unlinkSync(path.join(__dirname,file))}
for(const item of manifest.competitions){const related=manifest.competitions.filter(other=>other.id!==item.id&&other.category===item.category).slice(0,3).map(other=>'<a class="related-card" href="'+encodeURIComponent(other.id)+'.html"><div class="related-year">'+esc(other.year)+' · '+esc(other.category)+'</div><div class="related-title">'+esc(other.title)+'</div><span class="small-link">查看詳情 →</span></a>').join('')||'<div class="note">尚無同分類作品。</div>';const tags=item.tags.map(tag=>'<span class="pill">'+esc(tag)+'</span>').join('');const spotlightItems=(item.spotlight||[]).map(point=>'<li>'+esc(point)+'</li>').join('');const html=inject(template,{title:esc(item.title),year:esc(item.year),category:esc(item.category),summary:esc(item.summary),awardText:esc(item.award||'作品整理'),focus:esc(deriveFocus(item)),storyLead:esc(deriveStoryLead(item)),purpose:esc(derivePurpose(item)),ideation:esc(deriveIdeation(item)),model:esc(deriveModel(item)),execution:esc(deriveExecution(item)),highlights:esc(deriveHighlights(item)),outcomes:esc(deriveOutcomes(item)),storyShowcase:renderStoryShowcase(item,spotlightItems),teamBlock:renderTeamBlock(item),gradient:categoryGradients[item.category]||categoryGradients.default,awardBadge:'<span class="pill award">'+esc(item.award||'作品介紹')+'</span>',tags,spotlight:spotlightItems?'<ul class="spotlight-list">'+spotlightItems+'</ul>':'',appGallery:renderAppGallery(item),related,json:JSON.stringify({id:item.id,title:item.title,category:item.category,year:item.year}).replace(/<\//g,'<\\/')});fs.writeFileSync(path.join(__dirname,item.id+'.html'),html,'utf8')}
updateIndex(manifest.competitions);
const awarded=manifest.competitions.filter(item=>item.awarded).length;
const fileCount=manifest.competitions.reduce((sum,item)=>sum+item.files.length,0);
console.log('Generated '+manifest.competitions.length+' pages. '+awarded+' with awards, '+(manifest.competitions.length-awarded)+' without. '+fileCount+' files indexed.');
