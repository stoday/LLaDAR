const pptxgen = require('pptxgenjs');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'OpenAI';
pptx.subject = 'Agents 偏見檢測與緩解框架';
pptx.title = '邁向可信任 AI：開發者必備的 Agents 偏見檢測與緩解框架';
pptx.company = 'OpenAI';
pptx.lang = 'zh-TW';
pptx.theme = {
  headFontFace: 'Microsoft JhengHei',
  bodyFontFace: 'Microsoft JhengHei',
  lang: 'zh-TW'
};

const C = {
  ink: '17202A', muted: '5C6770', paper: 'F7F8FA', white: 'FFFFFF',
  navy: '17324D', teal: '168C8C', mint: 'DDF4EF', coral: 'E66A5D',
  amber: 'F0B44D', lilac: 'E9E5F5', blue: 'DDEAF7', line: 'D8DEE5',
  dark: '102A43', red: 'B83B3B', green: '28745A'
};
const W = 13.333, H = 7.5;
let slideNo = 0;

function addText(slide, text, x, y, w, h, opts={}) {
  slide.addText(text, {x,y,w,h, margin: 0, fontFace: opts.fontFace || 'Microsoft JhengHei', fontSize: opts.fontSize || 18,
    color: opts.color || C.ink, bold: !!opts.bold, italic: !!opts.italic, breakLine: false,
    valign: opts.valign || 'mid', align: opts.align || 'left', fit: 'shrink', paraSpaceAfterPt: opts.paraSpaceAfterPt || 0,
    bullet: opts.bullet, indent: opts.indent, transparency: opts.transparency, isTextBox: true});
}
function box(slide, x,y,w,h, fill, radius=0.16, lineColor=fill) {
  slide.addShape(radius ? pptx.ShapeType.roundRect : pptx.ShapeType.rect, {x,y,w,h,rectRadius: radius,fill:{color:fill},line:{color:lineColor,transparency: lineColor===fill?100:0, width:1}});
}
function line(slide,x1,y1,x2,y2,color=C.line,width=1.5,dash='solid') {
  slide.addShape(pptx.ShapeType.line,{x:x1,y:y1,w:x2-x1,h:y2-y1,line:{color,width,dashType:dash,beginArrowType:'none',endArrowType:'none'}});
}
function title(slide, kicker, heading, sub='') {
  addText(slide,kicker.toUpperCase(),0.65,0.38,11.8,0.24,{fontSize:10,bold:true,color:C.teal});
  addText(slide,heading,0.65,0.72,12.0,0.55,{fontSize:28,bold:true,color:C.navy});
  if(sub) addText(slide,sub,0.67,1.38,12.0,0.32,{fontSize:13,color:C.muted});
}
function footer(slide, section='TRUSTWORTHY AI') {
  slideNo += 1;
  line(slide,0.65,7.08,12.68,7.08,C.line,0.8);
  addText(slide,section,0.65,7.17,3.5,0.16,{fontSize:8,bold:true,color:C.muted});
  addText(slide,String(slideNo).padStart(2,'0'),12.15,7.15,0.5,0.18,{fontSize:9,bold:true,color:C.teal,align:'right'});
}
function notes(slide, text) { slide.addNotes(text); }
function pill(slide, text, x,y,w, fill=C.mint, color=C.teal) { box(slide,x,y,w,0.34,fill,0.17); addText(slide,text,x,y+0.01,w,0.3,{fontSize:10,bold:true,color,align:'center'}); }
function card(slide, x,y,w,h, head, body, accent=C.teal, fill=C.white) {
  box(slide,x,y,w,h,fill,0.14,C.line);
  slide.addShape(pptx.ShapeType.ellipse,{x:x+0.18,y:y+0.18,w:0.18,h:0.18,fill:{color:accent},line:{color:accent,transparency:100}});
  addText(slide,head,x+0.48,y+0.13,w-0.65,0.3,{fontSize:15,bold:true,color:C.navy});
  addText(slide,body,x+0.2,y+0.55,w-0.4,h-0.7,{fontSize:12,color:C.muted,valign:'top'});
}
function arrow(slide,x,y,w,color=C.teal) { line(slide,x,y,x+w,y,color,2); slide.addShape(pptx.ShapeType.chevron,{x:x+w-0.12,y:y-0.11,w:0.22,h:0.22,fill:{color},line:{color,transparency:100}}); }

// 1
{
  const s=pptx.addSlide(); s.background={color:C.navy};
  addText(s,'TRUSTWORTHY AI / DEVELOPER TALK',0.72,0.62,7,0.25,{fontSize:11,bold:true,color:C.mint});
  addText(s,'邁向可信任 AI',0.72,1.38,7.2,0.62,{fontSize:34,bold:true,color:C.white});
  addText(s,'開發者必備的 Agents 偏見檢測與緩解框架',0.72,2.14,8.8,0.9,{fontSize:28,bold:true,color:C.white,valign:'top'});
  addText(s,'從模型輸出，到整條決策路徑的系統責任',0.75,3.38,7.6,0.38,{fontSize:16,color:C.mint});
  box(s,9.3,1.15,2.65,4.55,C.teal,0.28);
  addText(s,'目標',9.7,1.58,1.8,0.3,{fontSize:13,bold:true,color:C.mint});
  addText(s,'公平\n可追溯\n可覆核',9.7,2.02,1.9,1.45,{fontSize:26,bold:true,color:C.white,valign:'top'});
  addText(s,'AGENT\nBIAS\nCONTROL',9.7,4.42,1.8,0.75,{fontSize:12,bold:true,color:C.navy,valign:'top'});
  addText(s,'演講時間：約 30 分鐘',0.75,6.33,4,0.25,{fontSize:11,color:'B7CDD9'}); footer(s,'OPENING');
  notes(s,'開場（約 30 秒）：今天不只談模型是否有偏見，而是談 Agent 如何把偏見帶進搜尋、排序、工具呼叫與現實行動。');
}

// 2
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'01 / 開場','當有偏見的模型開始替我們做事','同一句偏見，當 Agent 能夠行動時，後果不再只停留在文字裡。');
  card(s,0.75,2.0,3.55,3.35,'一般 LLM','「誰比較適合當工程師？」\n\n偏見出現在回答中，仍需要人閱讀、判斷與執行。',C.muted,C.white);
  arrow(s,4.55,3.5,0.62,C.coral);
  card(s,5.35,2.0,4.2,3.35,'AI Agent','讀履歷 → 搜尋資料 → 排序人選 → 寄出面試邀請\n\n偏見沿著流程變成真實世界的資源分配。',C.coral,'FFF0ED');
  box(s,10.05,2.0,2.45,3.35,C.navy,0.16);
  addText(s,'影響',10.35,2.35,1.8,0.3,{fontSize:13,bold:true,color:C.mint});
  addText(s,'工作\n金融\n醫療\n公共服務',10.35,2.9,1.7,1.8,{fontSize:22,bold:true,color:C.white,valign:'top'});
  addText(s,'核心問題',0.8,5.85,1.5,0.25,{fontSize:12,bold:true,color:C.coral});
  addText(s,'Agent 的偏見只是語言模型偏見，還是會因為規劃、記憶、工具與自主行動而產生新的風險？',2.1,5.77,10.0,0.5,{fontSize:18,bold:true,color:C.navy}); footer(s,'OPENING');
  notes(s,'開場（約 3 分鐘）：先請聽眾想像履歷篩選情境。強調 Agent 的差異在於它不只回答，還會替組織採取行動。');
}

// 3
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'02 / 基本結構','什麼是 Agent？一個會循環的決策系統','它把目標拆成步驟，使用工具取得資訊，再依結果調整下一步。');
  const nodes=[['目標','使用者希望完成什麼'],['規劃','將任務拆成步驟'],['模型','推理與決策'],['工具','搜尋、資料庫、程式、郵件'],['記憶','保存經驗與使用者資訊'],['行動與回饋','執行後調整下一步']];
  nodes.forEach((n,i)=>{ const x=0.75+(i%3)*4.05, y=2.15+Math.floor(i/3)*1.75; card(s,x,y,3.4,1.25,n[0],n[1],i===3?C.coral:C.teal,C.paper); if(i<2) arrow(s,x+3.48,y+0.63,0.48); if(i===2) line(s,x+1.7,y+1.3,x+1.7,y+1.55,C.teal,1.5,'dash'); });
  line(s,2.45,5.4,10.9,5.4,C.teal,2,'dash'); addText(s,'每一次工具呼叫與記憶寫入，都可能改變下一步的決策空間。',2.2,5.68,9.0,0.35,{fontSize:17,bold:true,color:C.navy,align:'center'}); footer(s,'AGENT ANATOMY');
  notes(s,'第二段（約 2 分鐘）：把 Agent 當成系統，不是單一模型。這個循環觀點是後面分析偏見來源的基礎。');
}

// 4
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'02 / 比較','為什麼 Agent 的偏見更複雜？','偏見可能藏在輸出之外：搜尋什麼、相信什麼、做什麼。');
  const rows=[['主要產生文字','可能採取實際行動'],['通常是單次回答','多步驟、持續決策'],['偏見較容易在輸出中被看見','偏見可能藏在搜尋、排序與工具選擇中'],['影響多半停留在資訊層面','可能影響工作、金融、醫療與公共服務']];
  box(s,0.75,2.0,5.7,0.55,C.navy,0.08); box(s,6.45,2.0,5.95,0.55,C.teal,0.08);
  addText(s,'一般 LLM',1.0,2.1,5.0,0.25,{fontSize:16,bold:true,color:C.white,align:'center'}); addText(s,'AI Agent',6.7,2.1,5.4,0.25,{fontSize:16,bold:true,color:C.white,align:'center'});
  rows.forEach((r,i)=>{ const y=2.55+i*0.83; box(s,0.75,y,5.7,0.78,i%2?C.white:'F0F3F6',0.03); box(s,6.45,y,5.95,0.78,i%2?C.white:'EAF7F4',0.03); addText(s,r[0],1.0,y+0.16,5.1,0.4,{fontSize:14,color:C.ink}); addText(s,r[1],6.7,y+0.16,5.3,0.4,{fontSize:14,bold:i>1,color:i>1?C.coral:C.ink}); });
  addText(s,'判斷單位從「一句回答」變成「整條決策路徑」。',1.0,6.2,11.3,0.4,{fontSize:20,bold:true,color:C.navy,align:'center'}); footer(s,'AGENT ANATOMY');
  notes(s,'第二段（約 2 分鐘）：逐列比較。請特別停在第三、四列，說明風險從可見文字，延伸到不可見的流程選擇。');
}

// 5
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'03 / 來源','Agent 的偏見：六個相互連結的層次','不要只問「模型有沒有偏見」，要問偏見在哪個環節被引入、被放大、被固化。');
  const items=[['01','模型偏見','刻板印象被當成合理判斷'],['02','目標與指令','「適合」未定義；效率壓過公平'],['03','資料與檢索','曝光度高的資料被過度採用'],['04','工具與流程','查詢詞、資料庫、評分器都會偏'],['05','記憶與回饋','一次推測變事實，形成循環'],['06','多代理人','彼此引用，形成假共識']];
  items.forEach((it,i)=>{ const x=0.75+(i%3)*4.1, y=2.1+Math.floor(i/3)*1.7; box(s,x,y,3.5,1.25,i===5?'FFF0ED':C.paper,0.14,C.line); addText(s,it[0],x+0.2,y+0.18,0.48,0.3,{fontSize:16,bold:true,color:i===5?C.coral:C.teal}); addText(s,it[1],x+0.78,y+0.16,2.4,0.3,{fontSize:15,bold:true,color:C.navy}); addText(s,it[2],x+0.2,y+0.62,3.1,0.4,{fontSize:12,color:C.muted}); });
  addText(s,'偏見不只是一個模型問題，而是一個系統問題。',1.1,6.0,11.0,0.4,{fontSize:21,bold:true,color:C.coral,align:'center'}); footer(s,'BIAS SOURCES');
  notes(s,'第三段（約 2 分鐘）：先展示全貌。接下來用幾張投影片把六個層次拆開，讓聽眾看到它們如何彼此串接。');
}

// 6
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'03 / 來源','前四層：偏見如何進入 Agent','偏見可能在「定義任務」以前就已經被寫進系統。');
  card(s,0.75,2.0,2.85,3.5,'模型偏見','訓練資料裡的性別、職業、族群與文化刻板印象。\n\n常見關聯，不等於合理判斷。',C.coral,C.white);
  card(s,3.78,2.0,2.85,3.5,'目標與指令','「找最適合的人」中的「適合」沒有定義。\n\n使用者的假設可能被 Agent 直接繼承。',C.amber,C.white);
  card(s,6.81,2.0,2.85,3.5,'資料與檢索','搜尋結果不是完整世界。\n\n資料稀少、曝光不足，容易被誤判成能力不足。',C.teal,C.white);
  card(s,9.84,2.0,2.75,3.5,'工具與流程','查詢詞、資料庫、評分工具的選擇，會改變誰被看見。\n\n中立工具串接後也可能累積偏差。',C.navy,C.white);
  addText(s,'設計提醒：先把「成功」與「公平」都寫成可檢查的要求。',0.95,6.05,11.5,0.4,{fontSize:17,bold:true,color:C.navy,align:'center'}); footer(s,'BIAS SOURCES');
  notes(s,'第三段（約 3 分鐘）：用履歷篩選來對應四層來源。提醒大家，bias audit 的第一個問題不是「模型說了什麼」，而是「任務被怎麼定義」。');
}

// 7
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'03 / 來源','後兩層：偏見如何被放大與固化','當 Agent 能記住、回饋、互相引用，偏見就可能變成系統慣性。');
  card(s,0.9,2.0,5.45,3.55,'05 / 記憶與回饋偏見','一次「資料不足」的推測，被寫入記憶，下一輪便被當成事實。\n\n越常選擇某類人 → 系統越認為這類人更適合 → 下一輪更常選擇同類人。',C.coral,'FFF0ED');
  card(s,6.98,2.0,5.45,3.55,'06 / 多代理人偏見','多個 Agent 彼此引用，不代表偏見會消失。\n\n評審 Agent 可能與執行 Agent 共享相同資料、模型與盲點，形成「彼此同意」的假共識。',C.teal,'EAF7F4');
  arrow(s,2.2,6.05,8.0,C.coral); addText(s,'記憶讓過去影響未來；多代理人讓單一盲點看起來像共識。',1.0,6.2,11.3,0.35,{fontSize:17,bold:true,color:C.navy,align:'center'}); footer(s,'BIAS SOURCES');
  notes(s,'第三段（約 2 分鐘）：強調時間維度。偏見不只存在於一次執行，也可能在記憶與回饋中累積。');
}

// 8
{
  const s=pptx.addSlide(); s.background={color:C.navy}; title(s,'04 / 案例','案例：履歷篩選 Agent','500 份履歷 → 20 位 AI 工程師候選人：偏見不是一句話，而是一條鏈。');
  const steps=[['01','定義優秀','學校、公司、工作連續性'],['02','補充資料','搜尋網路曝光與履歷缺口'],['03','形成推測','育兒中斷、非典型背景'],['04','排序決策','資料不足被當成能力不足'],['05','固化結果','排序存入系統，成為下一輪依據']];
  steps.forEach((st,i)=>{ const x=0.72+i*2.48; box(s,x,2.35,2.0,2.35,i===3?'8D3D48':'214B66',0.15); addText(s,st[0],x+0.18,2.58,0.5,0.25,{fontSize:16,bold:true,color:C.mint}); addText(s,st[1],x+0.18,3.0,1.62,0.35,{fontSize:16,bold:true,color:C.white}); addText(s,st[2],x+0.18,3.58,1.62,0.7,{fontSize:12,color:'D5E4E9',valign:'top'}); if(i<4) arrow(s,x+2.05,3.5,0.34,C.coral); });
  addText(s,'每一個局部判斷都「看似合理」，串起來卻可能排除同一批人。',1.0,5.55,11.3,0.45,{fontSize:21,bold:true,color:C.white,align:'center'}); footer(s,'CASE STUDY');
  notes(s,'第四段（約 3 分鐘）：完整講解履歷篩選案例。請明確指出，這裡不需要一個明顯歧視句子，整條 pipeline 就可能產生不公平結果。');
}

// 9
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'04 / 案例','放大的關鍵：資料不足 ≠ 能力不足','Agent 很容易把「看不見」誤讀成「不存在」。');
  addText(s,'原始履歷',0.95,2.0,1.6,0.28,{fontSize:13,bold:true,color:C.navy}); addText(s,'搜尋與排序',4.2,2.0,1.8,0.28,{fontSize:13,bold:true,color:C.navy}); addText(s,'系統記憶',7.45,2.0,1.6,0.28,{fontSize:13,bold:true,color:C.navy}); addText(s,'下一輪決策',10.45,2.0,1.8,0.28,{fontSize:13,bold:true,color:C.navy});
  box(s,0.9,2.45,2.55,2.15,C.white,0.14,C.line); addText(s,'非典型背景\n網路曝光少\n職涯有中斷',1.2,2.8,1.9,1.2,{fontSize:20,bold:true,color:C.coral,valign:'top'});
  arrow(s,3.55,3.5,0.5,C.coral);
  box(s,4.2,2.45,2.55,2.15,C.white,0.14,C.line); addText(s,'資料不足\n↓\n能力評分下降',4.55,2.9,1.8,1.1,{fontSize:19,bold:true,color:C.navy,align:'center'});
  arrow(s,6.85,3.5,0.5,C.coral);
  box(s,7.5,2.45,2.55,2.15,C.white,0.14,C.line); addText(s,'排序結果\n被保存成\n「成功模式」',7.84,2.8,1.9,1.2,{fontSize:19,bold:true,color:C.navy,align:'center'});
  arrow(s,10.15,3.5,0.5,C.coral);
  box(s,10.8,2.45,1.65,2.15,'FFF0ED',0.14,C.coral); addText(s,'偏差\n累積',11.05,3.0,1.15,0.8,{fontSize:22,bold:true,color:C.coral,align:'center'});
  addText(s,'要審計的是整條路徑：輸入 → 檢索 → 推論 → 排序 → 記憶 → 行動。',1.0,5.65,11.3,0.4,{fontSize:18,bold:true,color:C.navy,align:'center'}); footer(s,'CASE STUDY');
  notes(s,'第四段（約 2 分鐘）：用「資料不足 ≠ 能力不足」這句話做記憶點。把注意力從輸出文字轉向資料流與決策流。');
}

// 10
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'05 / 緩解框架','三層防線：事前、事中、事後','去偏見不是 Prompt 裡的一句提醒，而是整個 Agent 系統的工程設計。');
  const layers=[['事前','設計公平的任務','定義標準、辨識代理變數、預想受影響群體',C.teal,C.mint],['事中','限制與監督決策','留痕、分權、交叉檢查、高風險人工核准',C.amber,'FFF3D9'],['事後','評測與持續監控','群體指標、配對測試、多輪測試、申訴覆核',C.coral,'FFF0ED']];
  layers.forEach((l,i)=>{ const y=2.0+i*1.42; box(s,0.85,y,1.25,0.95,l[4],0.13,l[4]); addText(s,l[0],1.0,y+0.22,0.95,0.4,{fontSize:15,bold:true,color:l[3],align:'center'}); box(s,2.35,y,3.1,0.95,l[3],0.13,l[3]); addText(s,l[1],2.58,y+0.22,2.65,0.4,{fontSize:16,bold:true,color:C.white}); addText(s,l[2],5.85,y+0.19,6.2,0.5,{fontSize:15,color:C.navy}); });
  addText(s,'系統責任 = 設計選擇 + 執行控制 + 事後證據',1.0,6.4,11.3,0.35,{fontSize:19,bold:true,color:C.navy,align:'center'}); footer(s,'MITIGATION FRAMEWORK');
  notes(s,'第五段（約 2 分鐘）：先給出框架，再逐層展開。強調三層缺一不可：只做事前設計，無法知道實際執行是否偏；只做事後評測，也可能已造成不可逆傷害。');
}

// 11
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'05 / 事前','事前：把公平寫進任務設計','先決定「什麼算好」，再讓 Agent 開始找答案。');
  const bullets=['明確定義評估標準：區分必要條件與代理變數','避免以姓名、性別、年齡或居住地推測能力','在系統提示加入公平性與不確定性要求','先分析哪些群體可能受到不利影響'];
  bullets.forEach((b,i)=>{ const y=2.05+i*0.75; slideDot(s,1.0,y,C.teal); addText(s,b,1.35,y-0.08,7.0,0.35,{fontSize:16,color:C.ink}); });
  box(s,9.25,2.1,2.8,2.75,C.navy,0.2); addText(s,'任務契約',9.65,2.48,2.0,0.3,{fontSize:16,bold:true,color:C.mint,align:'center'}); addText(s,'目標\n評估標準\n不可使用的代理變數\n需要人工介入的情況',9.58,3.05,2.1,1.3,{fontSize:15,bold:true,color:C.white,align:'center',valign:'top'});
  addText(s,'問題不是「Prompt 夠不夠好」，而是「任務是否可被公平地執行」。',1.0,6.1,11.3,0.4,{fontSize:18,bold:true,color:C.navy,align:'center'}); footer(s,'MITIGATION FRAMEWORK');
  notes(s,'第五段（約 2 分鐘）：事前防線的重點是任務契約。請提醒開發者，把「公平」轉成可檢查的條件與限制。');
}

function slideDot(slide,x,y,color){ slide.addShape(pptx.ShapeType.ellipse,{x,y:y+0.05,w:0.16,h:0.16,fill:{color},line:{color,transparency:100}}); }

// 12
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'05 / 事中','事中：限制 Agent 的決策與行動','讓每個重要選擇都能被看見、被挑戰、被攔下。');
  const items=[['可觀測','保留工具呼叫、資料來源與推理軌跡'],['可分權','搜尋、評分、決策、執行權限分開'],['可質疑','要求反例、替代解釋與不確定性'],['可覆核','獨立模型/評審交叉檢查；高風險行動人工核准']];
  items.forEach((it,i)=>{ const x=0.85+(i%2)*6.1,y=2.0+Math.floor(i/2)*1.75; card(s,x,y,5.45,1.3,it[0],it[1],i===3?C.coral:C.teal,i===3?'FFF0ED':C.paper); });
  addText(s,'高風險、不可逆的行動：預設「人先批准」，不是「事後通知」。',1.0,6.1,11.3,0.4,{fontSize:18,bold:true,color:C.coral,align:'center'}); footer(s,'MITIGATION FRAMEWORK');
  notes(s,'第五段（約 2 分鐘）：事中防線是權限與監督設計。以寄信、拒絕申請、核貸、醫療建議等不可逆或高風險行動為例，預設需要人工核准。');
}

// 13
{
  const s=pptx.addSlide(); s.background={color:C.paper}; title(s,'05 / 事後','事後：把偏見變成可量測、可追蹤的訊號','評估單次回答不夠；要測整個 Agent 在多輪執行後的行為。');
  const metrics=[['群體差異','通過率、錯誤率、資源分配'],['配對測試','只改變性別、姓名或族群線索'],['多輪測試','觀察記憶與回饋是否累積偏差'],['治理機制','申訴、覆核、決策回復與責任紀錄']];
  metrics.forEach((m,i)=>{ const x=0.9+i*3.02; box(s,x,2.15,2.55,2.4,i===1?'FFF3D9':i===3?'FFF0ED':C.white,0.14,C.line); addText(s,String(i+1).padStart(2,'0'),x+0.2,2.43,0.45,0.25,{fontSize:16,bold:true,color:i===3?C.coral:C.teal}); addText(s,m[0],x+0.2,2.9,2.05,0.35,{fontSize:16,bold:true,color:C.navy}); addText(s,m[1],x+0.2,3.55,2.05,0.62,{fontSize:13,color:C.muted,valign:'top'}); });
  addText(s,'建立基線 → 反覆測試 → 設定門檻 → 觸發覆核 → 修正並留證',1.0,5.55,11.3,0.4,{fontSize:18,bold:true,color:C.navy,align:'center'}); footer(s,'MITIGATION FRAMEWORK');
  notes(s,'第五段（約 3 分鐘）：事後評測不要只看平均準確率。要比較不同群體、不同輪次、不同工具路徑，並建立可申訴與可回復的機制。');
}

// 14
{
  const s=pptx.addSlide(); s.background={color:C.white}; title(s,'06 / 限制','去偏見本身也有兩難','工程控制可以降低風險，但不能替人決定所有價值取捨。');
  const points=['公平沒有唯一的數學定義；不同指標可能互相衝突','移除敏感欄位，不代表其他欄位不會成為代理變數','要求模型完全「看不見差異」，有時反而無法補償既有不平等','多一個評審 Agent，不代表一定更公平'];
  points.forEach((p,i)=>{ const y=2.0+i*0.78; slideDot(s,1.0,y,C.coral); addText(s,p,1.35,y-0.1,8.3,0.42,{fontSize:16,color:C.ink}); });
  box(s,10.0,2.0,2.1,3.4,C.navy,0.18); addText(s,'最後的問題',10.3,2.42,1.5,0.3,{fontSize:14,bold:true,color:C.mint,align:'center'}); addText(s,'誰來決定？',10.23,3.18,1.65,0.75,{fontSize:24,bold:true,color:C.white,align:'center'}); addText(s,'可接受的價值取捨\n需要人負責',10.23,4.35,1.65,0.55,{fontSize:13,color:C.white,align:'center'}); footer(s,'LIMITS');
  notes(s,'第六段（約 2 分鐘）：避免把公平說成單一分數。公平指標的衝突與補償性措施，最後都需要組織明確承擔價值選擇。');
}

// 15
{
  const s=pptx.addSlide(); s.background={color:C.navy}; title(s,'07 / 結語','從模型去偏見，走向系統責任','三句話帶走今天的框架。');
  const qs=[['01','Agent 的偏見不只來自模型，也來自目標、資料、工具、記憶與回饋。'],['02','Agent 能夠行動，因此小偏差可能經過多步決策被放大。'],['03','真正的去偏見需要評測、權限控制、人工監督與持續治理。']];
  qs.forEach((q,i)=>{ const y=2.0+i*1.05; addText(s,q[0],0.95,y,0.55,0.35,{fontSize:16,bold:true,color:C.mint}); addText(s,q[1],1.8,y-0.04,10.2,0.48,{fontSize:20,bold:true,color:C.white}); });
  addText(s,'可信任 AI 不是「沒有偏見」的承諾，而是「能發現、能限制、能負責」的能力。',0.95,5.75,11.4,0.55,{fontSize:19,bold:true,color:C.mint,align:'center'}); footer(s,'CLOSING');
  notes(s,'結語（約 1 分鐘）：依序念出三句話，將焦點從模型責任拉到系統責任。');
}

// 16
{
  const s=pptx.addSlide(); s.background={color:C.teal};
  addText(s,'留給大家的問題',0.85,1.1,5.0,0.35,{fontSize:16,bold:true,color:C.mint});
  addText(s,'當 Agent 做出不公平的決定時，',0.85,2.0,11.2,0.62,{fontSize:30,bold:true,color:C.white});
  addText(s,'責任屬於模型、資料、工具、設計者，\n還是授權它行動的人？',0.85,2.88,11.0,1.2,{fontSize:31,bold:true,color:C.white,valign:'top'});
  box(s,0.9,5.45,3.1,0.52,C.navy,0.26); addText(s,'讓 Agent 值得被信任',1.15,5.58,2.6,0.25,{fontSize:15,bold:true,color:C.white,align:'center'}); footer(s,'DISCUSSION');
  notes(s,'收尾（約 1 分鐘）：不要急著給標準答案。把問題留給聽眾，邀請他們從自身的 Agent 系統中找出責任鏈。');
}

pptx.writeFile({ fileName: 'trustworthy_ai_agent_bias_framework.pptx' });

