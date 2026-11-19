'use strict';
const content = document.querySelector('#content');
let METHODOLOGY = '', V = '';
const label = html => html.replaceAll('{VU}',V.toUpperCase()).replaceAll('{V}',V);
const state = {stage: 'first', variant: 'adaptive', date: '', data: null};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (n, digits=1) => n === undefined || n === null ? '—' : Number(n).toFixed(digits);
const pct = n => n === undefined || n === null ? '—' : `${(n*100).toFixed(1)}%`;
const signed = (n, digits=1) => n === undefined || n === null ? '—' : `${n>0?'+':''}${num(n,digits)}`;
const time = x => new Date(x).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
const date = x => new Date(x+'T12:00:00Z').toLocaleDateString([], {month:'short',day:'numeric',year:'numeric'});
const page = document.body.dataset.page;
document.querySelector('#theme').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('sleeper-theme',theme); } catch (_) { /* Optional preference. */ }
  document.querySelectorAll('img[data-team]').forEach(img => { if(!img.dataset.fallback)img.src=teamLogoSource(img.dataset.team); });
});
const stat = (label,value,note,accent='') => `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value ${accent}">${value}</div><div class="stat-note">${note}</div></div>`;
const payout = odds => odds>0 ? odds/100 : 100/-odds;
const implied = odds => odds>0 ? 100/(100+odds) : -odds/(100-odds);
const empty = (title,text) => `<div class="empty"><strong>${title}</strong>${text}</div>`;
const hero = (tag,title,text) => `<div class="hero"><div><p class="eyebrow">${tag}</p><h1>${title}</h1><p class="subtext">${text}</p></div><div class="date-label">${date(state.date || state.data.today)}</div></div>`;
function controls() {
  const dates = [...new Set([...state.data.games.map(g=>g.slate_date),...state.data.forecasts.map(f=>f.game.slate_date)])].sort().reverse();
  const stages=['best','latest','first','lock'].map(s=>`<button data-stage="${s}" class="${state.stage===s?'active':''}" aria-pressed="${state.stage===s}">${s==='best'?'Tracked picks':s==='latest'?'Latest preview':s==='first'?'First snapshot':'5-hour lock'}</button>`).join('');
  return `<div class="controls"><div class="segmented" aria-label="Forecast stage">${stages}</div>${dateSelect(dates)}</div>`;
}
const dateSelect = dates => dates.length?`<select id="date-select" aria-label="Slate date">${dates.map(d=>`<option value="${d}" ${d===state.date?'selected':''}>${date(d)}</option>`).join('')}</select>`:'';
function teamLogoSource(code) {
  return /^[A-Z]{2,4}$/.test(code) ? `assets/teams/${code.toLowerCase()}-${document.documentElement.dataset.theme==='light'?'light':'dark'}.webp` : 'assets/team-placeholder.svg';
}
content.addEventListener('error', event => {
  const img=event.target;
  if(img instanceof HTMLImageElement && img.classList.contains('team-logo') && !img.dataset.fallback) {
    img.dataset.fallback='true';
    img.src='assets/team-placeholder.svg';
  }
},true);
function favoriteBenchmark(b) {
  if(!b?.n)return '';
  const row=(label,m)=>`<tr><td>${label}</td><td>${b.n}</td><td>${m.wins}–${m.losses}</td><td>${signed(m.units,2)}u</td><td>${pct(m.roi)}</td></tr>`;
  return `<section class="panel"><h2>Model picks versus market favorites</h2><p class="section-note">One unit per game at the same saved prices. Same pick on <strong>${b.agreements} of ${b.n}</strong> games.${b.pickems_excluded?` ${b.pickems_excluded} pick’em quote${b.pickems_excluded===1?'':'s'} left out of both rows.`:''}</p><div class="table-wrap"><table><caption class="sr-only">Matched model and favorite returns</caption><thead><tr><th>Strategy</th><th>Games</th><th>W–L</th><th>Net units</th><th>ROI</th></tr></thead><tbody>${row('{V} pick',b.model)}${row('Market favorite',b.favorite)}</tbody></table></div></section>`;
}
function versusMarket(m) {
  const row=(title,values,modelKey,marketKey,digits)=>{
    const n=values?.n||0, model=values?.[modelKey], market=values?.[marketKey];
    const winner=!n||model===null||market===null?'—':Math.abs(model-market)<1e-9?'Tie':model<market?'{V}':'Market';
    return `<tr><td>${title}</td><td>${n}</td><td>${num(model,digits)}</td><td>${num(market,digits)}</td><td>${winner}</td></tr>`;
  };
  return `<section class="panel" id="market-comparison"><h2>Forecast versus market</h2><p class="section-note">Saved DraftKings quotes paired with each forecast. Lower is better. Market win probabilities have the bookmaker margin removed.</p><div class="table-wrap"><table><caption class="sr-only">Forecast and market errors</caption><thead><tr><th>Measure</th><th>Games</th><th>{V}</th><th>Market</th><th>Lower error</th></tr></thead><tbody>${row('Win-probability log loss',m?.win_probability,'model_log_loss','market_log_loss',4)}${row('Margin error · points',m?.margin,'model_mae','market_mae',2)}${row('Total error · points',m?.total,'model_mae','market_mae',2)}</tbody></table></div></section>`;
}
// One record per game of the pooled {V} season record (replayed and live games together).
function seasonRows() { return state.data.season?.rows||[]; }
function rowRecord(r) {
  const side=r.margin>0?'home':r.margin<0?'away':null, homeWon=r.actual_home_score>r.actual_away_score;
  const won=side?(side==='home')===homeWon:null, price=side&&r.ml?r.ml[side]:null;
  let favorite='—';
  if(r.ml){const h=implied(r.ml.home), a=implied(r.ml.away), q=h/(h+a);favorite=h===a?'Pick’em':`${esc(q>.5?r.home:r.away)} <span class="small">${pct(Math.max(q,1-q))}</span>`;}
  const units=price===null||price===undefined?null:won?payout(price):-1;
  return {
    matchup:`${esc(r.away)} @ ${esc(r.home)}`,
    pick:side?`${esc(r[side])} <span class="small">${pct(side==='home'?r.wp_home:1-r.wp_home)}</span>`:'Even projection',
    price:price===null||price===undefined?'—':signed(price,0),
    favorite, final:`${r.actual_away_score} / ${r.actual_home_score}`,
    units, unitsText:units===null?'—':`${signed(units,2)}u`,
    won, status:won===null?'No pick':won?'Correct':'Incorrect', accent:won===null?'':won?'green':'red'
  };
}
function seasonStats(p) {
  const v=p.betting.directional;
  return `<div class="stats">${stat('Games',p.n,`${date(p.start_date)} – ${date(p.end_date)}`)}${stat('Winner record',`${p.correct}–${p.winner_count-p.correct}`,`${pct(p.accuracy)} of picks correct`)}${stat('Model-pick returns',v.n?`${signed(v.units,2)}u`:'—',v.n?`${pct(v.roi)} ROI · ${v.n} priced games`:'No saved moneylines')}</div>`;
}
function teamBlock(code,score,home) {
  return `<div><img class="team-logo" data-team="${esc(code)}" src="${teamLogoSource(code)}" alt="" width="40" height="40" loading="lazy" decoding="async"><div class="team-code">${esc(code)}</div><div class="team-name">${home?'Home':'Away'}</div><div class="score">${num(score)}</div></div>`;
}
function pickSummary(output,game,forecast) {
  if(!output) return '<div class="pick-summary"><p class="small">Model pick awaiting projection.</p></div>';
  const side = output.winner;
  if(side!=='home' && side!=='away') return '<div class="pick-summary"><p class="small">Even projection · no model winner.</p></div>';
  const moneyline = output.markets?.find(m=>m.market==='moneyline'&&m.eligible);
  const odds = moneyline?.prices?.[side];
  const probability = output[`wp_${side}`];
  const untracked = !!output.recommendations;
  const source=untracked?'Live preview · untracked':forecast?.stage==='lock'?'5-hour lock · tracked':'First snapshot · tracked';
  return `<div class="pick-summary"><div><span class="pick-label">MODEL PICK</span><strong>${esc(game[side])} moneyline</strong><small>${source}</small></div><div><strong class="green">${pct(probability)}</strong><small>${odds===undefined||odds===null?'Moneyline unavailable':`${signed(odds,0)} saved odds`}</small></div></div>`;
}
function lineupFor(forecast) {
  if(forecast?.lineup?.length)return forecast.lineup;
  return [];
}
function teamPlayers(team,players) {
  const projected=players.filter(p=>p.team===team&&p.projection_status==='projected'&&p.minutes!==null).sort((a,b)=>b.minutes-a.minutes||String(a.name).localeCompare(String(b.name))).slice(0,8);
  const flagged=players.filter(p=>p.team===team&&p.injury_status).sort((a,b)=>String(a.name).localeCompare(String(b.name)));
  const playerRows=projected.length?projected.map(p=>`<div class="player-row"><span><strong>${esc(p.name||'Unknown player')}</strong>${p.injury_status?` <em>${esc(p.injury_status)}</em>`:''}</span><span>${num(p.minutes,0)} min · ${num(p.points)} pts</span></div>`).join(''):'<p class="small">No player projections saved.</p>';
  const availability=flagged.length?`<div class="availability"><span>Availability</span>${flagged.map(p=>`<small><strong>${esc(p.name||'Unknown player')}</strong> · ${esc(p.injury_status)}</small>`).join('')}</div>`:'<div class="availability"><span>Availability</span><small>No Sleeper injury flags at this snapshot.</small></div>';
  return `<section class="team-players"><h4>${esc(team)} · projected rotation · top 8 by minutes</h4>${playerRows}${availability}</section>`;
}
function gameDetails(forecast,game,output,ats,totals) {
  const players=lineupFor(forecast);
  const flags=players.filter(p=>p.injury_status).length;
  const scoring=output?.scoring||forecast?.raw;
  const spread=ats?`${esc(game.home)} ${signed(ats.line)}`:'No spread saved';
  const total=totals?`O/U ${num(totals.line)}`:'No total saved';
  const correction=output?.matchup_adjustment?`${esc(game.away)} ${signed(output.matchup_adjustment.away_correction)} · ${esc(game.home)} ${signed(output.matchup_adjustment.home_correction)}`:'—';
  const playerNote=players.length?'Sleeper does not identify confirmed starters. The eight players with the most projected minutes are shown as the projected rotation. Availability reflects the saved snapshot.':'Player details were not retained with this snapshot. Model and market context remain available.';
  return `<details class="game-details"><summary><span>Players, availability & model details</span><small>${players.length?(flags?`${flags} injury flag${flags===1?'':'s'}`:'No injury flags'):'Player detail unavailable'}</small></summary><div class="details-body"><p class="details-note">${playerNote}</p>${players.length?`<div class="player-grid">${teamPlayers(game.away,players)}${teamPlayers(game.home,players)}</div>`:''}<div class="context-grid"><div><span>Model margin</span><strong>${scoring?signed(scoring.margin):'—'}</strong></div><div><span>Saved spread</span><strong>${spread}</strong></div><div><span>Model total</span><strong>${scoring?num(scoring.total):'—'}</strong></div><div><span>Saved total</span><strong>${total}</strong></div><div><span>Raw Sleeper</span><strong>${forecast?`${num(forecast.raw.away_score)}–${num(forecast.raw.home_score)}`:'—'}</strong></div><div><span>Defense adjustment</span><strong>${correction}</strong></div></div></div></details>`;
}

// The latest DraftKings home spread saved with any forecast or preview of the game.
function savedSpread(game) {
  const saved=[...state.data.forecasts,...state.data.previews].filter(f=>f.instance_id===game.instance_id&&!f.retired).sort((a,b)=>new Date(b.captured_at)-new Date(a.captured_at));
  for(const f of saved){const m=f.variants?.adaptive?.markets?.find(m=>m.market==='ats'&&Number.isFinite(m.line)&&new Date(m.captured_at)<new Date(game.tip_utc));if(m)return m.line;}
  return null;
}
function card(game,forecast) {
  if(forecast) game={...game,home:forecast.game.home,away:forecast.game.away};
  const out = forecast?.variants[state.variant];
  const market = kind => out?.markets.find(m=>m.market===kind);
  const ats=market('ats'), totals=market('totals');
  const lock = state.data.lock_outcomes?.[game.instance_id]?.status;
  const status = forecast ? (forecast.stage==='lock'?`Tracked · ${num(forecast.lead_hours,1)}h lock`:forecast.stage==='latest'?'Preview · untracked':'Tracked · first snapshot') : (state.stage==='latest'?(game.status==='pre'?'Awaiting preview':'Preview closed'):state.stage==='lock'&&lock==='locked'?'Locked under earlier version':state.stage==='lock'&&lock==='missed'?'Missed lock':game.status!=='pre'||new Date(game.tip_utc)<=new Date()?'No {V} capture':'Awaiting projection');
  return `<article class="game-card" data-gid="${esc(game.game_id)}" data-state="upcoming" data-live-spread="${savedSpread(game)??''}"><div class="card-head"><span>${time(game.tip_utc)} · ${esc(game.away)} at ${esc(game.home)}</span><span class="badge live" data-live-status hidden></span><span class="badge ${forecast?'valid':''}" data-track-status>${status}</span></div>${pickSummary(out,game,forecast)}<div class="matchup">${teamBlock(game.away,out?.scoring?.away_score ?? forecast?.raw.away_score,false)}<div class="versus">AT<small>PROJECTED</small></div>${teamBlock(game.home,out?.scoring?.home_score ?? forecast?.raw.home_score,true)}</div><div class="live-row" data-live-row hidden><b data-side="away"></b><span><small data-live-label>Live score</small><i data-live-clock style="font-style:normal"></i></span><b data-side="home"></b></div><div class="live-wp" data-live-wp hidden><div class="lwp-head"><small>Live win chance</small><span>Score, clock and saved spread</span></div><div class="lwp-row"><span>${esc(game.away)} <em data-wp="away"></em></span><div class="lwp-bar"><i></i></div><span><em data-wp="home"></em> ${esc(game.home)}</span></div></div>${out?`<div class="win-bar"><div class="win-labels"><span>${esc(game.away)} <strong>${pct(out.wp_away)}</strong></span><span>${esc(game.home)} <strong class="green">${pct(out.wp_home)}</strong></span></div><div class="bar" aria-label="${esc(game.away)} win probability ${pct(out.wp_away)}; ${esc(game.home)} win probability ${pct(out.wp_home)}"><span style="width:${out.wp_away*100}%"></span><span style="width:${out.wp_home*100}%"></span></div></div>`:''}${gameDetails(forecast,game,out,ats,totals)}<div class="card-foot"><span>${forecast?`${time(forecast.captured_at)} snapshot · ${esc(state.variant)} {V}`:'No accepted forecast for this stage'}</span><span>${forecast?`${num(forecast.lead_hours,2)}h before tip`:'No betting record'}</span></div></article>`;
}
function today() {
  const games=state.data.games.filter(g=>g.slate_date===state.date).sort((a,b)=>a.tip_utc.localeCompare(b.tip_utc));
  const forecasts=(state.stage==='latest'?state.data.previews:state.data.forecasts).filter(f=>!f.retired&&f.stage===state.stage&&f.game.slate_date===state.date);
  const firstCount=state.data.forecasts.filter(f=>!f.retired&&f.stage==='first'&&f.game.slate_date===state.date).length;
  const lockCount=games.filter(g=>state.data.lock_outcomes?.[g.instance_id]?.status==='locked').length;
  const viewNote=state.stage==='latest'?'Latest previews update with Sleeper and are not added to the tracked record.':'Tracked picks are frozen before tip and graded after the final score.';
  content.innerHTML=hero('{VU} · SLEEPER NBA',"Today's model picks",'Every game’s projected winner, win probability and recorded moneyline. Open a game for player projections, availability and model context.')+`<div class="stats">${stat('Games',games.length,'On this slate')}${stat('Tracked',`${firstCount} <span class="small">/ ${games.length}</span>`,'First snapshots frozen','green')}${stat('5-hour locks',`${lockCount} <span class="small">/ ${games.length}</span>`,'All versions · captured near T−5h')}</div><div class="toolbar"><h2>Today’s games</h2>${controls()}</div><p class="section-note">${viewNote} Tip times use your local time zone; slate dates use Eastern Time. <span data-scores-asof hidden>Live scores from ESPN, updated <time></time>; they are display only and never change a frozen forecast.</span></p><div class="grid">${games.length?games.map(g=>{let f=forecasts.find(f=>f.instance_id===g.instance_id);if(state.stage==='best'){const saved=state.data.forecasts.filter(f=>!f.retired&&f.instance_id===g.instance_id).sort((a,b)=>new Date(b.captured_at)-new Date(a.captured_at));const previews=state.data.previews.filter(f=>f.instance_id===g.instance_id).sort((a,b)=>new Date(b.captured_at)-new Date(a.captured_at));f=saved.find(f=>f.stage==='lock')||saved.find(f=>f.stage==='first')||previews[0];}return card(g,f);}).join(''):empty('No recorded games','The next successful schedule capture will populate this slate.')}</div><div class="notice">Projected scores use Sleeper player projections plus the {V} defense adjustment. Saved DraftKings lines provide context. Picks are research records; no wagers are placed.</div>`;
}
function results() {
  const season=state.data.season;
  if(!season?.pooled?.n){content.innerHTML=hero('{VU} · RESULTS','Predictions, resolved.','Every {V} model pick against the final score.')+empty('No results yet','Results appear once the first games are final.');return;}
  const dates=[...new Set(seasonRows().map(r=>r.slate_date))].sort().reverse();
  const records=seasonRows().filter(r=>r.slate_date===state.date).map(rowRecord);
  const picks=records.filter(r=>r.won!==null), correct=picks.filter(r=>r.won).length;
  const bets=records.filter(r=>r.units!==null), units=bets.reduce((sum,r)=>sum+r.units,0);
  const table=`<div class="table-wrap results-table"><table><caption class="sr-only">Results for ${date(state.date)}</caption><thead><tr><th>Matchup</th><th>Model pick · probability</th><th>Saved odds</th><th>Market favorite · fair probability</th><th>Final · away / home</th><th>Net units</th><th>Result</th></tr></thead><tbody>${records.map(r=>`<tr><td>${r.matchup}</td><td>${r.pick}</td><td>${r.price}</td><td>${r.favorite}</td><td>${r.final}</td><td>${r.unitsText}</td><td class="${r.accent}">${r.status}</td></tr>`).join('')}</tbody></table></div><div class="results-cards">${records.map(r=>`<article class="result-card"><div class="result-card-head"><h3>${r.matchup}</h3><span class="${r.accent}">${r.status}</span></div><dl><div><dt>Model pick</dt><dd>${r.pick}</dd></div><div><dt>Saved odds</dt><dd>${r.price}</dd></div><div><dt>Market favorite</dt><dd>${r.favorite}</dd></div><div><dt>Net units</dt><dd>${r.unitsText}</dd></div><div><dt>Final · away / home</dt><dd>${r.final}</dd></div></dl></article>`).join('')}</div>`;
  content.innerHTML=hero('{VU} · RESULTS','Predictions, resolved.','Every {V} model pick against the final score, one slate at a time.')+`<section aria-labelledby="results-title"><div class="toolbar filters-first"><h2 id="results-title">${date(state.date)}</h2><div class="controls">${dateSelect(dates)}</div></div><div class="stats">${stat('Games',records.length,'On this slate')}${stat('Winner record',picks.length?`${correct}–${picks.length-correct}`:'—',picks.length?`${pct(correct/picks.length)} of picks correct`:'No picks')}${stat('Model-pick returns',bets.length?`${signed(units,2)}u`:'—',bets.length?`${pct(units/bets.length)} ROI · ${bets.length} priced games`:'No saved moneylines')}</div>${table}<p class="section-note">One unit on each pick at the saved DraftKings moneyline. A dash means no moneyline was saved with the forecast; the pick still counts in the winner record. Slate dates use Eastern Time.</p></section><section aria-labelledby="season-title"><div class="toolbar"><h2 id="season-title">${esc(season.season)} season to date</h2><a href="performance.html">Full performance</a></div>${seasonStats(season.pooled)}</section>`;
}
// Rolling ROI of the model-pick series: one unit per pick at the saved moneyline, in tip order.
const ROI_WINDOW=50, ROI_SEASON_MIN=20;
function roiPoints() {
  const rows=seasonRows().slice().sort((a,b)=>a.tip_utc<b.tip_utc?-1:a.tip_utc>b.tip_utc?1:a.game_id<b.game_id?-1:1);
  const bets=[];
  for(const r of rows){const rec=rowRecord(r);if(rec.units!==null)bets.push({date:r.slate_date,matchup:`${r.away} @ ${r.home}`,units:rec.units});}
  let cum=0;
  return bets.map((b,i)=>{
    cum+=b.units;
    const k=i+1, recent=k>=ROI_WINDOW?bets.slice(k-ROI_WINDOW,k).reduce((a,x)=>a+x.units,0)/ROI_WINDOW:null;
    return {...b,k,cum,season:k>=ROI_SEASON_MIN?cum/k:null,rolling:recent};
  });
}
function roiPanel() {
  const pts=roiPoints(); if(pts.length<ROI_WINDOW)return '';
  const ends=[], seen=new Set();
  for(let i=pts.length-1;i>=0;i--)if(!seen.has(pts[i].date)){seen.add(pts[i].date);ends.unshift(pts[i]);}
  const roiText=v=>v===null?'—':`${v>0?'+':''}${(v*100).toFixed(1)}%`;
  const table=`<details class="roi-table"><summary>Show as table</summary><div class="table-wrap"><table><caption class="sr-only">Model-pick ROI by slate</caption><thead><tr><th>Slate</th><th>Bets to date</th><th>Net units</th><th>Season ROI</th><th>Last ${ROI_WINDOW} bets</th></tr></thead><tbody>${ends.slice().reverse().map(p=>`<tr><td>${date(p.date)}</td><td>${p.k}</td><td>${signed(p.cum,2)}u</td><td>${roiText(p.k>=ROI_SEASON_MIN?p.cum/p.k:null)}</td><td>${roiText(p.rolling)}</td></tr>`).join('')}</tbody></table></div></details>`;
  return `<h3 class="roi-title">Rolling return on investment</h3><p class="section-note">Model-pick ROI after each bet, in tip order. Season to date starts at bet ${ROI_SEASON_MIN}; the rolling line covers the latest ${ROI_WINDOW} bets.</p><div class="roi-legend" aria-hidden="true"><span><i class="key season"></i>Season to date</span><span><i class="key rolling"></i>Last ${ROI_WINDOW} bets</span></div><div class="roi-chart" id="roi-chart" role="img" aria-label="Line chart of model-pick ROI by bet. Season to date ${roiText(pts.at(-1).season)}, last ${ROI_WINDOW} bets ${roiText(pts.at(-1).rolling)}."></div>${table}`;
}
function drawRoi() {
  const host=document.querySelector('#roi-chart'); if(!host)return;
  const pts=roiPoints(), W=Math.max(300,host.clientWidth), H=W<560?220:260;
  const m={l:46,r:W<560?14:104,t:12,b:28}, iw=W-m.l-m.r, ih=H-m.t-m.b, n=pts.length;
  const vals=pts.flatMap(p=>[p.season,p.rolling]).filter(v=>v!==null);
  let lo=Math.min(0,...vals), hi=Math.max(0,...vals);
  const step=(hi-lo)>0.3?0.1:0.05; lo=Math.floor(lo/step-0.25)*step; hi=Math.ceil(hi/step+0.25)*step;
  const x=k=>m.l+(n<=1?0:(k-1)/(n-1)*iw), y=v=>m.t+(hi-v)/(hi-lo)*ih;
  const path=key=>{let d='',on=false;for(const p of pts){if(p[key]===null){on=false;continue;}d+=`${on?'L':'M'}${x(p.k).toFixed(1)},${y(p[key]).toFixed(1)}`;on=true;}return d;};
  const pctLabel=v=>`${v>0?'+':''}${Math.round(v*100)}%`;
  let grid='';
  for(let v=lo;v<=hi+1e-9;v+=step){const yy=y(v).toFixed(1), zero=Math.abs(v)<1e-9;grid+=`<line x1="${m.l}" x2="${m.l+iw}" y1="${yy}" y2="${yy}" class="${zero?'roi-zero':'roi-grid'}"/><text x="${m.l-8}" y="${yy}" dy="0.32em" text-anchor="end" class="roi-axis">${pctLabel(Math.abs(v)<1e-9?0:v)}</text>`;}
  const starts=[];let last=null;for(const p of pts){if(p.date!==last){starts.push(p);last=p.date;}}
  const tickEvery=Math.max(1,Math.ceil(starts.length/(W<560?3:6)));
  let xt='';starts.filter((_,i)=>i%tickEvery===0).forEach(p=>{xt+=`<text x="${x(p.k).toFixed(1)}" y="${m.t+ih+18}" text-anchor="middle" class="roi-axis">${new Date(p.date+'T12:00:00Z').toLocaleDateString([],{month:'short',day:'numeric'})}</text>`;});
  const end=pts.at(-1); let labels='';
  if(W>=560){
    let ys=y(end.season), yr=y(end.rolling); const gap=16;
    if(Math.abs(ys-yr)<gap){const mid=(ys+yr)/2, up=ys<=yr;ys=mid+(up?-gap/2:gap/2);yr=mid+(up?gap/2:-gap/2);}
    const lab=(yy,v,name)=>`<text x="${m.l+iw+10}" y="${yy.toFixed(1)}" dy="0.32em" class="roi-end"><tspan class="roi-end-value">${(v>0?'+':'')+(v*100).toFixed(1)}%</tspan> <tspan class="roi-axis">${name}</tspan></text>`;
    labels=lab(ys,end.season,'Season')+lab(yr,end.rolling,`Last ${ROI_WINDOW}`);
  }
  host.innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" focusable="false" aria-hidden="true">${grid}${xt}<path d="${path('season')}" class="roi-line season"/><path d="${path('rolling')}" class="roi-line rolling"/>${labels}<line class="roi-cross" y1="${m.t}" y2="${m.t+ih}" x1="0" x2="0" visibility="hidden"/><circle class="roi-dot season" r="4" visibility="hidden"/><circle class="roi-dot rolling" r="4" visibility="hidden"/><rect class="roi-hit" x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" tabindex="0" aria-label="ROI by bet; use arrow keys"/></svg><div class="roi-tip" hidden></div>`;
  const svg=host.querySelector('svg'), hit=host.querySelector('.roi-hit'), cross=host.querySelector('.roi-cross'), tip=host.querySelector('.roi-tip');
  const dots={season:host.querySelector('.roi-dot.season'),rolling:host.querySelector('.roi-dot.rolling')};
  const fmt=v=>v===null?'—':`${v>0?'+':''}${(v*100).toFixed(1)}%`;
  let idx=n-1;
  const show=i=>{
    idx=Math.max(0,Math.min(n-1,i)); const p=pts[idx], xx=x(p.k);
    cross.setAttribute('x1',xx);cross.setAttribute('x2',xx);cross.setAttribute('visibility','visible');
    for(const key of ['season','rolling']){const d=dots[key];if(p[key]===null){d.setAttribute('visibility','hidden');continue;}d.setAttribute('cx',xx);d.setAttribute('cy',y(p[key]));d.setAttribute('visibility','visible');}
    tip.innerHTML=`<div class="roi-tip-head">Bet ${p.k} · ${esc(date(p.date))}</div><div class="small">${esc(p.matchup)} · ${p.units>0?'+':''}${p.units.toFixed(2)}u</div><div class="roi-tip-row"><i class="key season"></i><strong>${fmt(p.season)}</strong> Season to date</div><div class="roi-tip-row"><i class="key rolling"></i><strong>${fmt(p.rolling)}</strong> Last ${ROI_WINDOW} bets</div>`;
    tip.hidden=false;
    const tw=tip.offsetWidth, left=xx+12+tw>W?xx-12-tw:xx+12; tip.style.left=`${Math.max(0,left)}px`; tip.style.top=`${m.t}px`;
  };
  const hide=()=>{cross.setAttribute('visibility','hidden');Object.values(dots).forEach(d=>d.setAttribute('visibility','hidden'));tip.hidden=true;};
  hit.addEventListener('pointermove',e=>{const r=svg.getBoundingClientRect();show(Math.round((e.clientX-r.left-m.l)/iw*(n-1)));});
  hit.addEventListener('pointerleave',hide);
  hit.addEventListener('focus',()=>show(idx));
  hit.addEventListener('blur',hide);
  hit.addEventListener('keydown',e=>{const d={ArrowLeft:-1,ArrowRight:1,Home:-n,End:n}[e.key];if(d!==undefined){e.preventDefault();show(idx+d);}});
}
let roiResize=null;
window.addEventListener('resize',()=>{clearTimeout(roiResize);roiResize=setTimeout(drawRoi,150);});
function performance() {
  const season=state.data.season;
  const head=hero('{VU} · PERFORMANCE','Model versus market.','The {V} season record against final scores and saved DraftKings prices.');
  if(!season?.pooled?.n){content.innerHTML=head+empty('No results yet','Performance appears once the first games are final.');return;}
  const p=season.pooled, b=p.betting;
  const defaults={wp_a:10,ats_bias:0,ats_sigma:14,totals_bias:0,totals_sigma:19};
  const param=state.data.calibrations?.first?.params||defaults;
  const series=(name,m)=>`<tr><td>${name}</td><td>${m.n}</td><td>${m.n?`${m.wins}–${m.losses}`:'—'}</td><td>${m.n?signed(m.units,2)+'u':'—'}</td><td>${pct(m.roi)}</td></tr>`;
  content.innerHTML=head+`<section aria-labelledby="season-title"><div class="toolbar filters-first"><h2 id="season-title">${esc(season.season)} season record</h2></div>${seasonStats(p)}<div class="stats">${stat('Log loss',num(p.log_loss,4),'Win probability · lower is better')}${stat('Margin error',num(p.margin_mae,2),'Mean absolute error · points')}${stat('Total error',num(p.total_mae,2),'Mean absolute error · points')}</div></section>${versusMarket(p.versus_market)}<section class="panel"><h2>Moneyline returns</h2><p class="section-note">One unit on each model pick at the saved DraftKings price.</p><div class="table-wrap"><table><thead><tr><th>Series</th><th>Bets</th><th>W–L</th><th>Net units</th><th>ROI</th></tr></thead><tbody>${series('Model winner',b.directional)}</tbody></table></div>${roiPanel()}</section>${favoriteBenchmark(p.favorite_benchmark)}<details class="technical-details" id="model-details"><summary>Model details</summary><h2>Parameters in use</h2><div class="parameter-grid">${[['WP scale',param.wp_a],['ATS bias',param.ats_bias],['ATS SD',param.ats_sigma],['Total bias',param.totals_bias],['Total SD',param.totals_sigma]].map(([name,value])=>stat(name,num(value),'points')).join('')}</div>${coverageTable()}${defenseTable()}</details>`;
}
function coverageTable() {
  const rows=(state.data.lock_coverage||[]).slice(-10).reverse(); if(!rows.length)return '';
  const locked=rows.reduce((a,r)=>a+r.locked,0), games=rows.reduce((a,r)=>a+r.games,0);
  return `<h2 style="margin-top:30px">Lock capture coverage</h2><p class="small">${locked} of ${games} elapsed lock windows captured across the last ${rows.length} reported slates, all versions. A lock is missed when no valid capture lands in its window (15 minutes after T−5h; through tip for locks frozen under v1.20.1); missed games have no lock record and are never backfilled.</p><div class="table-wrap"><table><thead><tr><th>Slate</th><th>Locked</th><th>Missed</th><th>Median lead</th><th>Shortest lead</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(r.slate_date)}</td><td>${r.locked} / ${r.games}</td><td>${r.missed}</td><td>${r.median_lead_hours===null?'—':num(r.median_lead_hours,1)+'h'}</td><td>${r.min_lead_hours===null?'—':num(r.min_lead_hours,1)+'h'}</td></tr>`).join('')}</tbody></table></div>`;
}
function defenseTable() {
  const v=state.data.matchup_calibrations?.first, S=state.data.matchup_settings; if(!v||!S)return '';
  const teams=Object.keys(v.params.defense).sort((a,b)=>v.params.defense[a]-v.params.defense[b]);
  const off=S.apply_offense&&v.params.offense_control;
  return `<h2 style="margin-top:30px">${off?'Team offense and defense adjustments':'Residual defense adjustments'}</h2><p class="small">${S.half_life_days}-calendar-day decay · zero-centered defensive prior: ${S.defense_prior} games · offense${off?'':' control'}: ${S.offense_prior} games. ${off?'Offense is added to the team\'s own projected points; defense to its opponent\'s. ':''}Negative defense values reduce opponent scoring. Fitted on ${v.training_count||0} games.</p><div class="table-wrap"><table><thead><tr><th>Team</th><th>Training games</th>${off?'<th>Own points adjustment</th>':''}<th>Opponent points adjustment</th></tr></thead><tbody>${teams.map(t=>`<tr><td>${esc(t)}</td><td>${v.team_counts?.[t]||0}</td>${off?`<td>${signed(v.params.offense_control[t])}</td>`:''}<td>${signed(v.params.defense[t])}</td></tr>`).join('')}</tbody></table></div>`;
}
function methodology() {
  const S=state.data.matchup_settings||{};
  content.innerHTML=hero('{VU} · THE MODEL','From players to probabilities.','Sleeper player projections, learned team offense and opponent-defense corrections, and separately reported historical and live results.')+`<div class="prose"><h2>01 / Start with every projected player</h2><p>Sum Sleeper projected points across each full roster. Projected minutes validate team coverage between 200 and 280 minutes; they do not rescale the score. Empty roster placeholders remain missing and contribute no assumed points. Partial or inconsistent projected rows invalidate the snapshot. Sleeper does not supply a confirmed-starter flag, so game cards show the eight highest projected-minute players as the projected rotation.</p><div class="formula">H_raw = sum of home player points<br>A_raw = sum of away player points</div><h2>02 / Apply the {V} correction</h2><p>Fit historical team-point residuals with global, venue, offense and opponent-defense terms. From version 1.25 each team's offense term is added to its own projected points and its defense term to its opponent's; through version 1.24 offense was an estimation control only. Team effects use 25-calendar-day decay. Zero-centered ridge priors are ${S.defense_prior??20} fresh observations for defense, ${S.offense_prior??20} for offense and 100 each for global and venue.</p><p>Version 1.25 applied offense and lowered the defensive prior from 40 to 20 at the owner's request, after a walk-forward test of 80 offense/defense combinations on this season's 207 games: margin error fell 0.05 points (95% interval −0.21 to +0.10) and win-probability log loss 0.003 (−0.016 to +0.007), neither distinguishable from zero. Last season's archive had pointed slightly the other way.</p><p>Version 1.21 raised the defensive prior from 5 to 40. With about eight games per team, a prior of 5 moved margins by several points on noise; in last season's archive it scored worse than raw Sleeper through January and worse on win-probability log loss over the full season. A prior of 40 keeps the correction small until a team's defensive residual is established. This was a retrospective choice on previously inspected data, made in the direction of more shrinkage.</p><div class="formula">H = H_raw + global + venue / 2 + offense(home) + defense(away)<br>A = A_raw + global − venue / 2 + offense(away) + defense(home)<br>M = H − A &nbsp; · &nbsp; T = H + A</div><p>Raw scores remain visible. The first-stage team correction is initialized from 117 audited current-season legacy first snapshots. From v1.23 the correction applies from the first graded game of a season, locks use the same team correction as first snapshots, and it is refitted once each slate is fully graded. Earlier locks carried no correction. Seasons are kept separate.</p><h2>03 / Calibrate confidence prospectively</h2><p>Win-probability scale fits prior winner outcomes. ATS and totals learn uncertainty from errors against the exact frozen {V} forecasts. Confidence calibration uses a 75-completed-game half-life, persistent shrinkage, a 30-game minimum, daily movement limits and retention of accepted parameters on fit failure. Additional scalar ATS and totals biases remain zero because global and venue corrections are already included in scoring.</p><div class="formula">Home win probability = Φ(M / a)<br>Home cover probability = Φ((M + home spread) / ATS SD)<br>Over probability = Φ((T − market total) / total SD)</div><p>Initial values are a = 10, ATS SD = 14 and totals SD = 19. Φ is the standard normal cumulative distribution. These probability models are calibrated independently. Results must have been verified before each new forecast's calibration cutoff. From version 1.24 the confidence history starts with this season's earlier games, replayed under the current method from results known before each game, and adds completed native {V} forecasts; a native game replaces its replay. Lock forecasts use the same calibration as first snapshots. In a sequential test on this season's games this lowered log loss by 0.0019, an amount not distinguishable from zero.</p><h2>04 / Preserve a fixed control</h2><p>The fixed benchmark uses raw Sleeper scores, a win-probability scale of 10, ATS SD of 14 and totals SD of 19. Public comparisons use the same {V} live games and contemporaneous market opportunities for both variants.</p><h2>05 / Freeze the evidence</h2><p>The first snapshot is write-once. From version 1.27 it is the first valid capture on the game's Eastern date that carries a DraftKings moneyline, or the first capture from T−5h if no price has appeared; earlier versions froze earlier captures, often about 40 hours ahead and before odds were posted. In the season record, a game whose frozen snapshot had no price is priced at the DraftKings close from ESPN; a snapshot price is never replaced. The lock is the first valid capture in the 15 minutes after T−5h, so every new lock has about the same lead time. A continuously running watcher wakes at each target. Locks frozen under the v1.20.1 policy were accepted until tip and keep that record; The Picks view falls back to the latest saved pregame snapshot, then the first snapshot, when no lock exists. Fallbacks are labeled and remain untracked. Latest previews are untracked and do not create bets or training observations. Major schedule changes retire the original instance and void its research selections.</p><h2>06 / Pick moneylines only</h2><p>ESPN supplies DraftKings two-sided prices. Quotes are timestamped when the response finishes; the sportsbook's own update time is unknown. Prices must satisfy pairing and pregame timing rules to create selections. The public pages display every model-predicted winner as a moneyline pick, regardless of price or edge. Historical research subsets remain in the audit ledger. One-unit returns use offered-price payouts and overtime-inclusive finals.</p><p>Spread and totals picks are retired. On this season's priced games the spread leans were an underdog bias produced by compressed margins, totals were a coin flip, and the market was more accurate at both. The model still forecasts margin and total, and the Performance page compares those forecasts with the market's lines, which is where an improvement would show first. Spread and totals selections recorded before v1.22 stay in the ledger.</p><p>The win-probability scale re-fits itself from completed games, which is the same adjustment as a slope on the margin. No separate slope is applied.</p><h2>07 / One season record</h2><p>Results and Performance show one season record under {V}: every first-snapshot game this season. Games forecast live under {V} count as frozen. Earlier games (archived first snapshots from before native capture began, and captures made under earlier versions) are replayed with the current method, chronologically, using only results that were known before each game, with the moneyline prices saved at the time. A game appears once.</p><p>Replayed games are scored after the fact and their result times are partly assumed; they also start the confidence history described above. Replay writes no forecast, pick or grade to the ledger. Locks are not replayed.</p><h2>08 / Live scores and win chance</h2><p>While a game is in progress, Today shows its score and clock from ESPN, read in your browser every 30 seconds, and a live win chance computed from the score, the time left and the DraftKings spread saved with the forecast: Φ(a·lead/√τ + b·μ·√τ), where μ is the pregame expected home margin and τ the share of regulation left. Its coefficients were fitted on play-by-play from 2015-16 to 2025-26. It is a display only: separate from the pregame forecast, never saved, and never part of a pick, grade or record. It is absent when no spread was saved.</p><div class="notice">The watcher runs on GitHub Actions and hands off to a fresh run every few hours; a watchdog restarts it if the chain breaks. If it is down for a whole lock window, that lock is missed and counted in the coverage table. A delayed run cannot manufacture an earlier capture. No wagers are executed.</div><p><a class="green" href="MODEL.md">Read the complete build specification ↗</a> &nbsp; <a class="green" href="https://github.com/Dave356w/NBA-Sleeper-Model">Inspect the source and audit records ↗</a></p></div>`;
}
// Live scores (display only): the reader's browser reads ESPN's public scoreboard for the
// selected slate, every 30 s while a game is in progress and at the next tip before that.
const live={date:null,events:new Map(),timer:null,waiting:false,fails:0,at:null};
const LIVE_MS=30000, MIN_MS=60000, MAX_MS=600000;
const period=p=>p<=4?`Q${p}`:p===5?'OT':`${p-4}OT`;
// In-game win chance (display only): Phi(a(tau)*lead/sqrt(tau) + b(tau)*mu*sqrt(tau)), mu = -(home spread),
// tau = share of regulation left. Coefficients arrive in data.live_wp (web/live_wp.json).
function interpolate(knots,values,t) {
  if(t<=knots[0])return values[0];
  for(let i=1;i<knots.length;i++)if(t<=knots[i])return values[i-1]+(t-knots[i-1])/(knots[i]-knots[i-1])*(values[i]-values[i-1]);
  return values.at(-1);
}
function normalCdf(z) { // Abramowitz & Stegun 7.1.26
  const x=Math.abs(z)/Math.SQRT2, t=1/(1+0.3275911*x);
  const y=1-((((1.061405429*t-1.453152027)*t+1.421413741)*t-0.284496736)*t+0.254829592)*t*Math.exp(-x*x);
  return z>=0?(1+y)/2:(1-y)/2;
}
const liveTau=(p,clock)=>p<=4?((4-p)*720+Math.min(Math.max(clock,0),720))/2880:Math.min(Math.max(clock,0),300)/2880;
function liveWinChance(lead,mu,t,fit) {
  if(t<=0){if(lead)return lead>0?1:0;t=300/2880;}
  t=Math.max(t,1/2880);
  return normalCdf(interpolate(fit.knots,fit.a,t)*lead/Math.sqrt(t)+interpolate(fit.knots,fit.b,t)*mu*Math.sqrt(t));
}
function clockSeconds(st) {
  if(typeof st.clock==='number')return st.clock;
  const parts=String(st.displayClock||'0').split(':');
  return parts.length>1?Number(parts[0])*60+Number(parts[1]):Number(parts[0])||0;
}
const chance=p=>p>0.995?'>99%':p<0.005?'<1%':`${Math.round(100*p)}%`;
function showWinChance(card,view) {
  const box=card.querySelector('[data-live-wp]'), fit=state.data.live_wp;
  if(!box)return;
  const spread=parseFloat(card.dataset.liveSpread), home=parseInt(view.scores.home,10), away=parseInt(view.scores.away,10);
  if(view.state!=='live'||!fit||!Number.isFinite(spread)||!Number.isFinite(home)||!Number.isFinite(away)||!(view.period>=1)){box.hidden=true;return;}
  const p=liveWinChance(home-away,-spread,liveTau(view.period,view.clock),fit);
  box.querySelector('[data-wp=home]').textContent=chance(p);
  box.querySelector('[data-wp=away]').textContent=chance(1-p);
  box.querySelector('.lwp-bar i').style.width=`${(100*(1-p)).toFixed(1)}%`;
  box.hidden=false;
}
function liveView(e) {
  const st=e.status||{}, ty=st.type||{}, scores={};
  (e.competitions?.[0]?.competitors||[]).forEach(c=>{scores[c.homeAway]=c.score;});
  if(ty.state==='in')return {state:'live',scores,period:Number(st.period),clock:clockSeconds(st),text:ty.name==='STATUS_HALFTIME'?'Halftime':ty.name==='STATUS_END_PERIOD'?`End ${period(st.period)}`:ty.name==='STATUS_IN_PROGRESS'?`${period(st.period)} ${st.displayClock}`:(ty.shortDetail||'Live')};
  if(ty.state==='post')return {state:'final',scores:ty.completed?scores:{},text:ty.shortDetail||'Final'};
  return null;
}
function applyLive() {
  if(page!=='index'||live.date!==state.date)return;
  let shown=0;
  document.querySelectorAll('.game-card[data-gid]').forEach(card=>{
    const view=live.events.get(card.dataset.gid);
    if(!view)return;
    card.dataset.state=view.state;
    card.style.order=view.state==='live'?0:2;
    const badge=card.querySelector('[data-live-status]');
    badge.textContent=view.text;badge.hidden=false;
    badge.className='badge'+(view.state==='live'?' live':'');
    const row=card.querySelector('[data-live-row]');
    if(view.scores.away!=null&&view.scores.home!=null){
      row.querySelectorAll('b').forEach(b=>{b.textContent=view.scores[b.dataset.side];});
      row.querySelector('[data-live-label]').textContent=view.state==='live'?'Live score':'Final score';
      row.querySelector('[data-live-clock]').textContent=view.text;
      row.hidden=false;
    }
    showWinChance(card,view);
    shown++;
  });
  document.querySelectorAll('.game-card[data-state="upcoming"]').forEach(card=>{card.style.order=1;});
  const asof=document.querySelector('[data-scores-asof]');
  if(shown&&asof&&live.at){asof.querySelector('time').textContent=time(live.at);asof.hidden=false;}
}
function loadLive() {
  const slate=live.date;
  fetch(`https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?limit=100&dates=${slate.replaceAll('-','')}`,{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(r.status);return r.json();}).then(j=>{
    if(live.date!==slate)return;
    live.fails=0;live.at=new Date();
    let active=0, soon=Infinity;
    (j?.events||[]).forEach(e=>{
      const view=liveView(e);
      if(view){live.events.set(String(e.id),view);if(view.state==='live')active++;}
      else if(e.status?.type?.state==='pre'){const tip=Date.parse(e.date);soon=Math.min(soon,Number.isFinite(tip)?tip-Date.now():MAX_MS);}
    });
    applyLive();
    if(active)nextLive(LIVE_MS);else if(soon<Infinity)nextLive(Math.min(Math.max(soon,MIN_MS),MAX_MS));
  }).catch(()=>{if(live.date===slate&&++live.fails<=10)nextLive(Math.min(MIN_MS*live.fails,MAX_MS));});
}
function nextLive(ms) {
  clearTimeout(live.timer);
  live.timer=setTimeout(()=>{if(document.hidden){live.waiting=true;return;}loadLive();},ms);
}
function syncLive() {
  if(page!=='index'||!window.fetch)return;
  if(live.date===state.date){applyLive();return;}
  clearTimeout(live.timer);
  Object.assign(live,{date:state.date,events:new Map(),waiting:false,fails:0,at:null});
  loadLive();
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&live.waiting){live.waiting=false;loadLive();}});
function render() {
  ({index:today,results,performance,methodology}[page] || today)();
  content.innerHTML=label(content.innerHTML); // One pass fills the version tokens before handlers attach.
  syncLive();
  drawRoi();
  document.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{state.stage=b.dataset.stage;render();});
  const select=document.querySelector('#date-select');if(select) select.onchange=()=>{state.date=select.value;render();};
}
fetch('data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('Recorded data could not be loaded.');return r.json();}).then(data=>{
  if(!data.methodology||!data.methodology_version)throw new Error('Reporting data is not available yet.');
  METHODOLOGY=data.methodology; V='v'+data.methodology_version;
  data.forecasts=data.forecasts.filter(f=>f.methodology===METHODOLOGY);
  data.previews=(data.previews||[]).filter(f=>f.methodology===METHODOLOGY);
  data.performance=data.performance_by_methodology?.[METHODOLOGY]||data.performance;
  state.data=data;state.date=data.today;if(page==='index')state.stage='best';
  const dates=[...new Set((page==='results'?(data.season?.rows||[]).map(r=>r.slate_date):data.games.map(g=>g.slate_date)))].sort();
  if(page==='results')state.date=dates.at(-1)||data.today;
  else if(!dates.includes(state.date)&&dates.length)state.date=dates.at(-1);
  document.querySelector('#updated').textContent=`Updated ${new Date(data.updated_at).toLocaleString()}`;
  render();
}).catch(e=>{content.innerHTML=`<div class="error-display"><h2>Recorded data unavailable</h2><p>${esc(e.message)} Refresh later or use the forecast download.</p></div>`;});
