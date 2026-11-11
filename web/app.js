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
try { document.documentElement.dataset.theme = localStorage.getItem('sleeper-theme') || 'dark'; } catch (_) { /* Storage may be disabled. */ }
document.querySelector('#theme').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('sleeper-theme',theme); } catch (_) { /* Optional preference. */ }
  document.querySelectorAll('img[data-team]').forEach(img => { if(!img.dataset.fallback)img.src=teamLogoSource(img.dataset.team); });
});
const stat = (label,value,note,accent='') => `<div class="stat"><div class="stat-label">${label}</div><div class="stat-value ${accent}">${value}</div><div class="stat-note">${note}</div></div>`;
const gates = () => state.data.sample_gates || {forecast_games:100,settled_bets:250};
const small = (n,min,unit) => (n||0) < min ? `Exploratory · ${n||0} of ${min} ${unit}` : '';
const empty = (title,text) => `<div class="empty"><strong>${title}</strong>${text}</div>`;
const hero = (tag,title,text) => `<div class="hero"><div><p class="eyebrow">${tag}</p><h1>${title}</h1><p class="subtext">${text}</p></div><div class="date-label">${date(state.date || state.data.today)}</div></div>`;
function controls(withDate=true) {
  const slates = state.data.forecasts.filter(f=>f.stage===state.stage).map(f=>f.game.slate_date);
  const dates = [...new Set(page==='index'?[...state.data.games.map(g=>g.slate_date),...slates]:slates)].sort().reverse();
  const stages=(page==='index'?['best','latest','first','lock']:['first','lock']).map(s=>`<button data-stage="${s}" class="${state.stage===s?'active':''}" aria-pressed="${state.stage===s}">${s==='best'?'Tracked picks':s==='latest'?'Latest preview':s==='first'?'First snapshot':'5-hour lock'}</button>`).join('');
  const variants=page==='index'?'':`<div class="segmented" aria-label="Model variant">${['adaptive','fixed'].map(v=>`<button data-variant="${v}" class="${state.variant===v?'active':''}" aria-pressed="${state.variant===v}">${v==='adaptive'?'{V}':'Fixed control'}</button>`).join('')}</div>`;
  return `<div class="controls"><div class="segmented" aria-label="Forecast stage">${stages}</div>${variants}${withDate&&dates.length?`<select id="date-select" aria-label="Slate date">${dates.map(d=>`<option value="${d}" ${d===state.date?'selected':''}>${date(d)}</option>`).join('')}</select>`:''}</div>`;
}
const modelName = () => state.variant==='adaptive' ? '{V}' : 'Fixed control';
const stageName = () => state.stage==='lock' ? '5-hour lock' : 'First snapshot';
const liveScope = () => `LIVE ONLY · ${stageName()} · ${modelName()}`;
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
function favoriteBenchmark(b, name) {
  if(!b?.n)return empty('Favorite benchmark awaiting paired prices','The model and market favorite are compared on the same priced games.');
  const row=(label,m)=>`<tr><td>${label}</td><td>${b.n}</td><td>${m.wins}–${m.losses}</td><td>${signed(m.units,2)}u</td><td>${pct(m.roi)}</td></tr>`;
  return `<div class="favorite-benchmark"><h3>Model picks versus market favorites</h3><p class="section-note">One unit per game at the same saved prices. Same pick on <strong>${b.agreements} of ${b.n}</strong> games. ${b.pickems_excluded?`${b.pickems_excluded} pick’em quote${b.pickems_excluded===1?'':'s'} excluded from both rows.`:''}</p><div class="table-wrap"><table><caption class="sr-only">Matched model and favorite returns</caption><thead><tr><th>Strategy</th><th>Games</th><th>W–L</th><th>Net units</th><th>ROI</th></tr></thead><tbody>${row(esc(name),b.model)}${row('Market favorite',b.favorite)}</tbody></table></div><p class="section-note">${small(b.n,gates().settled_bets,'bets')||'Descriptive returns.'} Choosing winners and beating their prices are separate questions.</p></div>`;
}
function versusMarket(comparison, name, scope, id) {
  const m=comparison?.versus_market;
  const row=(title,values,modelKey,marketKey,digits)=>{
    const n=values?.n||0, model=values?.[modelKey], market=values?.[marketKey];
    const winner=!n||model===null||market===null?'—':Math.abs(model-market)<1e-9?'Tie':model<market?'Model':'Market';
    return `<tr><td>${title}</td><td>${n}</td><td>${num(model,digits)}</td><td>${num(market,digits)}</td><td>${winner}</td></tr>`;
  };
  return `<section class="panel market-comparison" id="${id}" aria-labelledby="${id}-title"><p class="scope-label">${scope}</p><h2 id="${id}-title">Forecast versus market</h2><p class="section-note">Recorded DraftKings quotes paired with each forecast. Lower is better. Win probabilities remove the bookmaker margin. Counts use only games with an eligible quote for that measure; these are descriptive averages.</p><div class="table-wrap"><table><caption class="sr-only">${scope} forecast errors</caption><thead><tr><th>Measure</th><th>Games</th><th>${esc(name)}</th><th>Market</th><th>Lower error</th></tr></thead><tbody>${row('Win-probability log loss',m?.win_probability,'model_log_loss','market_log_loss',4)}${row('Margin error · points',m?.margin,'model_mae','market_mae',2)}${row('Total error · points',m?.total,'model_mae','market_mae',2)}</tbody></table></div>${favoriteBenchmark(comparison?.favorite_benchmark,name)}</section>`;
}
function seasonSummary() {
  const season=state.data.season;
  if(!season?.pooled?.n)return '';
  const p=season.pooled, v=p.betting.directional;
  const replayed=season.segments.filter(x=>x.key!=='live').reduce((a,x)=>a+(x.n||0),0);
  const live=season.segments.find(x=>x.key==='live')?.n||0;
  const thin=(n,min)=>n&&n<min?' <span class="small">· exploratory</span>':'';
  const bet=b=>b?.n?`${signed(b.units,2)}u · ${pct(b.roi)} <span class="small">(${b.n} bets)</span>${thin(b.n,gates().settled_bets)}`:'—';
  const row=(name,m,strong)=>`<tr class="${m.key==='live'?'live-part':''}"><td>${strong?'<strong>':''}${esc(name)}${strong?'</strong>':''}</td><td>${m.n||0}${thin(m.n,gates().forecast_games)}</td><td>${m.n?`${m.correct}–${m.winner_count-m.correct}`:'—'}</td><td>${pct(m.accuracy)}</td><td>${bet(m.betting?.directional)}</td></tr>`;
  return `<section class="panel historical-replay" aria-labelledby="season-title"><p class="scope-label">SEASON OVERVIEW · {V} · FIRST SNAPSHOTS</p><h2 id="season-title">${esc(season.season)} pooled record</h2><div class="provenance-pills"><span class="scope-pill">${live} live games</span><span class="scope-pill replay-pill">${replayed} historical replays</span></div><p>${date(p.start_date)}–${date(p.end_date)}. This overview combines <strong>${live} live</strong> and <strong>${replayed} replayed</strong> games. Replay is retrospective, was inspected before {V} was set, and was never live betting evidence. This block always shows {V} first snapshots; the live controls above do not change it.</p><div class="stats">${stat('Combined games',p.n,`${replayed} replay · ${live} live`)}${stat('Combined winner record',`${p.correct}–${p.winner_count-p.correct}`,`${pct(p.accuracy)} · replay + live`)}${stat('Combined model returns',v.n?`${signed(v.units,2)}u`:'—',v.n?`${pct(v.roi)} · ${v.n} priced games · replay + live`:'No paired moneylines')}</div><div class="table-wrap"><table><caption class="sr-only">Separate live and replay contributions</caption><thead><tr><th>Part</th><th>Games</th><th>W–L</th><th>Accuracy</th><th>Model-winner returns</th></tr></thead><tbody>${[...season.segments].sort((a,b)=>(a.key==='live'?-1:0)-(b.key==='live'?-1:0)).map(x=>row(x.label+(x.key==='live'?' {V}':''),x)).join('')}${row('Combined · replay + live',p,true)}</tbody></table></div><p class="section-note">One unit at the recorded price; unpriced games have no return. ${small(v.n,gates().settled_bets,'bets')||'Descriptive results.'} The live and replay parts remain separate evidence.</p><details class="replay-details"><summary>Replay assumptions and market comparison</summary><p class="section-note">${esc(season.replay_clock)} Replay creates no forecasts, bets, grades or live training observations. Results below ${gates().forecast_games} games or ${gates().settled_bets} settled bets are exploratory.</p>${versusMarket(p,'{V}','REPLAY + LIVE · FIRST SNAPSHOTS · {V}','season-market-comparison')}</details></section>`;
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

function card(game,forecast) {
  const out = forecast?.variants[state.variant];
  const market = kind => out?.markets.find(m=>m.market===kind);
  const ats=market('ats'), totals=market('totals');
  const missed = state.data.missed_locks.some(x=>x.instance_id===game.instance_id);
  const status = forecast ? (forecast.stage==='lock'?`Tracked · ${num(forecast.lead_hours,1)}h lock`:forecast.stage==='latest'?'Preview · untracked':'Tracked · first snapshot') : (state.stage==='latest'?(game.status==='pre'?'Awaiting preview':'Preview closed'):state.stage==='lock'&&missed?'Missed lock':game.status!=='pre'||new Date(game.tip_utc)<=new Date()?'No {V} capture':'Awaiting projection');
  return `<article class="game-card"><div class="card-head"><span>${time(game.tip_utc)} · ${esc(game.away)} at ${esc(game.home)}</span><span class="badge ${forecast?'valid':''}">${status}</span></div>${pickSummary(out,game,forecast)}<div class="matchup">${teamBlock(game.away,out?.scoring?.away_score ?? forecast?.raw.away_score,false)}<div class="versus">AT<small>PROJECTED</small></div>${teamBlock(game.home,out?.scoring?.home_score ?? forecast?.raw.home_score,true)}</div>${out?`<div class="win-bar"><div class="win-labels"><span>${esc(game.away)} <strong>${pct(out.wp_away)}</strong></span><span>${esc(game.home)} <strong class="green">${pct(out.wp_home)}</strong></span></div><div class="bar" aria-label="${esc(game.away)} win probability ${pct(out.wp_away)}; ${esc(game.home)} win probability ${pct(out.wp_home)}"><span style="width:${out.wp_away*100}%"></span><span style="width:${out.wp_home*100}%"></span></div></div>`:''}${gameDetails(forecast,game,out,ats,totals)}<div class="card-foot"><span>${forecast?`${time(forecast.captured_at)} snapshot · ${esc(state.variant)} {V}`:'No accepted forecast for this stage'}</span><span>${forecast?`${num(forecast.lead_hours,2)}h before tip`:'No betting record'}</span></div></article>`;
}
function today() {
  const games=state.data.games.filter(g=>g.slate_date===state.date).sort((a,b)=>a.tip_utc.localeCompare(b.tip_utc));
  const forecasts=(state.stage==='latest'?state.data.previews:state.data.forecasts).filter(f=>!f.retired&&f.stage===state.stage&&f.game.slate_date===state.date);
  const firstCount=state.data.forecasts.filter(f=>!f.retired&&f.stage==='first'&&f.game.slate_date===state.date).length;
  const lockCount=state.data.forecasts.filter(f=>!f.retired&&f.stage==='lock'&&f.game.slate_date===state.date).length;
  const viewNote=state.stage==='latest'?'Latest previews update with Sleeper and are not added to the tracked record.':'Tracked picks are frozen before tip and graded after the final score.';
  content.innerHTML=hero('{VU} · SLEEPER NBA',"Today's model picks",'Every game’s projected winner, win probability and recorded moneyline. Open a game for player projections, availability and model context.')+`<div class="stats">${stat('Games',games.length,'On this slate')}${stat('Tracked',`${firstCount} <span class="small">/ ${games.length}</span>`,'First snapshots frozen','green')}${stat('5-hour locks',`${lockCount} <span class="small">/ ${games.length}</span>`,'Captured near T−5h')}</div><div class="toolbar"><h2>Today’s games</h2>${controls()}</div><p class="section-note">${viewNote} Tip times use your local time zone; slate dates use Eastern Time.</p><div class="grid">${games.length?games.map(g=>{let f=forecasts.find(f=>f.instance_id===g.instance_id);if(state.stage==='best'){const saved=state.data.forecasts.filter(f=>!f.retired&&f.instance_id===g.instance_id).sort((a,b)=>new Date(b.captured_at)-new Date(a.captured_at));const previews=state.data.previews.filter(f=>f.instance_id===g.instance_id).sort((a,b)=>new Date(b.captured_at)-new Date(a.captured_at));f=saved.find(f=>f.stage==='lock')||saved.find(f=>f.stage==='first')||previews[0];}return card(g,f);}).join(''):empty('No recorded games','The next successful schedule capture will populate this slate.')}</div><div class="notice">Projected scores use Sleeper player projections plus the {V} defense adjustment. Saved DraftKings lines provide context. Picks are research records; no wagers are placed.</div>`;
}
function resultRecord(f) {
  const g=state.data.grades[f.id], output=f.variants[state.variant], graded=g?.variants[state.variant];
  const moneyline=output.markets?.find(m=>m.market==='moneyline'&&m.eligible);
  const side=output.winner, hasPick=side==='home'||side==='away';
  const favorite=moneyline?(moneyline.no_vig.home===moneyline.no_vig.away?null:moneyline.no_vig.home>moneyline.no_vig.away?'home':'away'):null;
  const bet=!g?.void?graded?.selections.find(s=>s.market==='moneyline'&&s.series==='directional'&&['win','loss','push'].includes(s.outcome)):null;
  return {
    f,g,graded,output,bet,
    matchup:`${esc(f.game.away)} @ ${esc(f.game.home)}`,
    pick:hasPick?`${esc(f.game[side])} <span class="small">${pct(output['wp_'+side])}</span>`:'Even projection',
    price:hasPick&&moneyline?signed(moneyline.prices[side],0):'—',
    favorite:favorite?`${esc(f.game[favorite])} <span class="small">${pct(moneyline.no_vig[favorite])}</span>`:moneyline?'Pick’em':'—',
    final:g?.result?`${g.result.away_score} / ${g.result.home_score}`:'—',
    units:bet?`${signed(bet.units,2)}u`:'—',
    status:g?.void?'Void':!g?'Pending':graded?.winner_correct===null?'No directional pick':graded?.winner_correct?'Correct':'Incorrect',
    accent:g?.void||!g?'':graded?.winner_correct?'green':graded?.winner_correct===false?'red':''
  };
}
function results() {
  const forecasts=state.data.forecasts.filter(f=>f.stage===state.stage&&f.game.slate_date===state.date).sort((a,b)=>a.game.tip_utc.localeCompare(b.game.tip_utc));
  const records=forecasts.map(resultRecord);
  const settled=records.filter(r=>r.g&&!r.g.void);
  const directional=settled.filter(r=>typeof r.graded?.winner_correct==='boolean');
  const correct=directional.filter(r=>r.graded.winner_correct).length;
  const bets=records.filter(r=>r.bet), units=bets.reduce((sum,r)=>sum+r.bet.units,0);
  const table=records.length?`<div class="table-wrap results-table"><table><caption class="sr-only">Live ${stageName()} results for ${date(state.date)}</caption><thead><tr><th>Matchup</th><th>Model pick · probability</th><th>Saved odds</th><th>Market favorite · fair probability</th><th>Final · away / home</th><th>Net units</th><th>Winner result</th></tr></thead><tbody>${records.map(r=>`<tr><td>${r.matchup}</td><td>${r.pick}</td><td>${r.price}</td><td>${r.favorite}</td><td>${r.final}</td><td>${r.units}</td><td class="${r.accent}">${r.status}</td></tr>`).join('')}</tbody></table></div><div class="results-cards">${records.map(r=>`<article class="result-card"><div class="result-card-head"><h3>${r.matchup}</h3><span class="${r.accent}">${r.status}</span></div><dl><div><dt>Model pick</dt><dd>${r.pick}</dd></div><div><dt>Saved odds</dt><dd>${r.price}</dd></div><div><dt>Market favorite</dt><dd>${r.favorite}</dd></div><div><dt>Net units</dt><dd>${r.units}</dd></div><div><dt>Final · away / home</dt><dd>${r.final}</dd></div><div><dt>Model</dt><dd>${modelName()}</dd></div></dl></article>`).join('')}</div>`:empty('No live forecasts for this selection','Select another recorded slate or stage. Live results begin with the first frozen {V} capture.');
  content.innerHTML=hero('{VU} · LIVE RESULTS','Predictions, resolved.','Frozen pregame forecasts, graded against the final score. Choose the slate, capture stage and model to inspect.')+`<section aria-labelledby="live-results-title"><div class="toolbar filters-first"><h2 id="live-results-title">Recorded outcomes</h2>${controls()}</div><p class="scope-label">${liveScope()} · ${date(state.date)} · EASTERN SLATE DATE</p><div class="stats">${stat('Graded live games',settled.length,'Selected slate, stage and model')}${stat('Winner record',directional.length?`${correct}–${directional.length-correct}`:'—',directional.length?`${pct(correct/directional.length)} · ${small(directional.length,gates().forecast_games,'games')||'directional picks'}`:'No directional picks')}${stat('Model-pick returns',bets.length?`${signed(units,2)}u`:'—',bets.length?`${pct(units/bets.length)} · ${bets.length} settled bets`:'No settled, paired moneylines')}</div>${table}<p class="section-note">Moneylines are captured with the forecast. A dash means no eligible paired price or settled bet. One unit is risked per priced pick; winner accuracy also includes unpriced games. No wagers are executed.</p></section>`;
  if(records.length)content.innerHTML+=`<details class="technical-details"><summary>Saved projections and forecast errors</summary><div class="table-wrap"><table><thead><tr><th>Game</th><th>Projected · away / home</th><th>Margin error</th><th>Total error</th></tr></thead><tbody>${records.map(r=>`<tr><td>${r.matchup}</td><td>${num(r.output.scoring?.away_score??r.f.raw.away_score)} / ${num(r.output.scoring?.home_score??r.f.raw.home_score)}</td><td>${signed(r.g?.void?null:r.graded?.ats_margin_error)}</td><td>${signed(r.g?.void?null:r.graded?.adjusted_total_error)}</td></tr>`).join('')}</tbody></table></div><p class="section-note">Errors are final score minus the calibrated prediction. Retired research subsets remain in the audit ledger.</p></details>`;
  content.innerHTML+=seasonSummary();
}
function reliabilityChart(rows) {
  if(!rows?.length)return empty('Awaiting completed games','Reliability compares predicted home-win probability with observed frequency.');
  return `<svg class="chart" viewBox="0 0 440 290" role="img" aria-label="Home win probability reliability chart"><line x1="45" y1="245" x2="415" y2="245"/><line x1="45" y1="245" x2="45" y2="20"/><line x1="45" y1="245" x2="415" y2="20" stroke-dasharray="5 5"/>${[0,.25,.5,.75,1].map(v=>`<text x="${45+370*v}" y="266" text-anchor="middle">${v*100}%</text><text x="36" y="${249-225*v}" text-anchor="end">${v*100}%</text>`).join('')}${rows.map(r=>`<circle cx="${45+370*r.predicted}" cy="${245-225*r.actual}" r="${Math.min(11,4+Math.sqrt(r.n))}"><title>${r.n} games · predicted ${pct(r.predicted)}, actual ${pct(r.actual)}</title></circle>`).join('')}<text x="230" y="286" text-anchor="middle">Predicted home-win probability</text></svg>`;
}
function performance() {
  const performanceData=state.data.performance, key=`${state.stage}:${state.variant}`;
  const metrics=performanceData.forecast[key];
  const comparison=state.data.market_comparisons?.[key];
  const thin=small(metrics?.n,gates().forecast_games,'games');
  const defaults={wp_a:10,ats_bias:0,ats_sigma:14,totals_bias:0,totals_sigma:19};
  const param=state.variant==='fixed'?defaults:(state.data.calibrations[state.stage]?.params||defaults);
  const bets=Object.entries(performanceData.betting).filter(([k])=>k.startsWith(`${key}:`)&&k.split(':')[3]==='moneyline'&&k.split(':').at(-1)==='directional');
  const liveReturns=`<section class="panel live-returns"><p class="scope-label">${liveScope()}</p><h2>Live moneyline returns</h2>`+(bets.length?`<div class="table-wrap"><table><thead><tr><th>Book · model winner</th><th>Bets</th><th>W–L–P</th><th>Net units</th><th>ROI</th></tr></thead><tbody>${bets.map(([k,b])=>`<tr><td>${esc(k.split(':')[2])}</td><td>${b.n}</td><td>${b.wins}–${b.losses}–${b.pushes}</td><td>${signed(b.units,2)}u</td><td>${pct(b.roi)}</td></tr>`).join('')}</tbody></table></div>`:empty('No settled moneyline picks','Returns begin when paired capture-time prices and final results exist.'))+`<p class="section-note">Frozen live forecasts only, with one unit risked per priced pick. ${gates().settled_bets} settled bets is the reporting threshold; a positive return alone does not demonstrate an edge. Replay appears in its own season overview below.</p></section>`;
  content.innerHTML=hero('{VU} · PERFORMANCE','Model versus market.','Compare frozen live forecasts with recorded market prices, then inspect the separate season overview with its replay and live parts.')+`<div class="toolbar filters-first"><h2>Live evaluation</h2>${controls(false)}</div><p class="scope-label">${liveScope()} · ALL GRADED LIVE GAMES</p><div class="stats">${stat('Completed live games',metrics?.n||0,`${stageName()} · ${modelName()}`)}${stat('Winner accuracy',pct(metrics?.accuracy),metrics?`${metrics.correct} of ${metrics.winner_count} directional picks`:'Awaiting graded forecasts')}${stat('Win-probability log loss',num(metrics?.log_loss,4),'All graded live games · lower is better')}</div>${thin?`<div class="notice"><strong>Exploratory sample.</strong> ${metrics?.n||0} graded live games. The reporting threshold is ${gates().forecast_games} games. These results are descriptive and do not yet demonstrate a market edge.</div>`:''}${versusMarket(comparison,modelName(),liveScope(),'live-market-comparison')}${liveReturns}${seasonSummary()}`;
  const paired=performanceData.paired[state.stage];
  const fixedTable=`<h2>{V} versus fixed control</h2><div class="table-wrap"><table><thead><tr><th>Model</th><th>Games</th><th>Accuracy</th><th>Log loss</th><th>Brier</th></tr></thead><tbody>${['fixed','adaptive'].map(v=>{const m=performanceData.forecast[`${state.stage}:${v}`];return `<tr><td>${v==='adaptive'?'{V}':'Fixed control'}</td><td>${m?.n||0}</td><td>${pct(m?.accuracy)}</td><td>${num(m?.log_loss,4)}</td><td>${num(m?.brier,4)}</td></tr>`}).join('')}</tbody></table></div>`;
  content.innerHTML+=`<details class="technical-details" id="model-diagnostics"><summary>Model diagnostics and capture coverage</summary><p class="scope-label">${liveScope()}</p><div class="two-col"><section class="panel"><h3>Probability reliability</h3><p class="section-note">The diagonal marks perfect calibration. Point size reflects game count.</p>${reliabilityChart(metrics?.reliability)}</section><section class="panel"><h3>Scoring errors</h3><p class="section-note">Final minus calibrated forecast, including overtime.</p><div class="table-wrap"><table><thead><tr><th>Measure</th><th>Margin</th><th>Total</th></tr></thead><tbody>${[['MAE','mae'],['RMSE','rmse'],['Bias','bias']].map(([title,k])=>`<tr><td>${title}</td><td>${num(metrics?.margin?.[k])}</td><td>${num(metrics?.total?.[k])}</td></tr>`).join('')}</tbody></table></div><p class="section-note">Brier score: ${num(metrics?.brier,4)}.</p></section></div><h2>Parameters in use</h2><div class="parameter-grid">${[['WP scale',param.wp_a],['ATS bias',param.ats_bias],['ATS SD',param.ats_sigma],['Total bias',param.totals_bias],['Total SD',param.totals_sigma]].map(([name,value])=>stat(name,num(value),'points')).join('')}</div>${fixedTable}${paired?`<p class="section-note">Paired adaptive − fixed log loss: ${num(paired.adaptive_minus_fixed_log_loss,4)} across ${paired.n} games / ${paired.slates} slates. ${paired.confidence_interval_95?`95% slate-bootstrap interval: ${paired.confidence_interval_95.map(x=>num(x,4)).join(' to ')}.`:'Insufficient slates for an interval.'} ${paired.exploratory?'Exploratory sample.':''}</p>`:''}${leadTable(metrics)}${coverageTable()}${defenseTable()}</details>`;
}

function leadTable(metrics) {
  const rows=metrics?.lead_buckets; if(!rows?.length)return '';
  return `<h2 style="margin-top:30px">Scores by capture lead time</h2><p class="small">Hours between the frozen capture and tip. Locks are taken within 15 minutes after T−5h. Locks frozen under the earlier through-tip policy could be much later and saw more injury news; compare like with like.</p><div class="table-wrap"><table><thead><tr><th>Lead time</th><th>Games</th><th>Accuracy</th><th>Log loss</th><th>Brier</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.bucket)}</td><td>${r.n}</td><td>${pct(r.accuracy)}</td><td>${num(r.log_loss,4)}</td><td>${num(r.brier,4)}</td></tr>`).join('')}</tbody></table></div>`;
}
function coverageTable() {
  const rows=(state.data.lock_coverage||[]).slice(-10).reverse(); if(!rows.length)return '';
  const locked=rows.reduce((a,r)=>a+r.locked,0), games=rows.reduce((a,r)=>a+r.games,0);
  return `<h2 style="margin-top:30px">Lock capture coverage</h2><p class="small">${locked} of ${games} games locked across the last ${rows.length} closed slates, all versions. A lock is missed when no valid capture lands in its window (15 minutes after T−5h; through tip for locks frozen under v1.20.1); missed games have no lock record and are never backfilled.</p><div class="table-wrap"><table><thead><tr><th>Slate</th><th>Locked</th><th>Missed</th><th>Median lead</th><th>Shortest lead</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(r.slate_date)}</td><td>${r.locked} / ${r.games}</td><td>${r.missed}</td><td>${r.median_lead_hours===null?'—':num(r.median_lead_hours,1)+'h'}</td><td>${r.min_lead_hours===null?'—':num(r.min_lead_hours,1)+'h'}</td></tr>`).join('')}</tbody></table></div>`;
}
function defenseTable() {
  const v=state.data.matchup_calibrations?.[state.stage], S=state.data.matchup_settings; if(!v||!S)return '';
  const teams=Object.keys(v.params.defense).sort((a,b)=>v.params.defense[a]-v.params.defense[b]);
  return `<h2 style="margin-top:30px">Residual defense adjustments</h2><p class="small">${S.half_life_days}-calendar-day decay · zero-centered defensive prior: ${S.defense_prior} games · offense control: ${S.offense_prior} games. Negative values reduce opponent scoring. ${v.seed_count||0} legacy-first and ${v.native_count||0} native training games; these are separate from forward performance.</p><div class="table-wrap"><table><thead><tr><th>Team</th><th>Training games</th><th>Opponent points adjustment</th></tr></thead><tbody>${teams.map(t=>`<tr><td>${esc(t)}</td><td>${v.team_counts?.[t]||0}</td><td>${signed(v.params.defense[t])}</td></tr>`).join('')}</tbody></table></div>`;
}
function methodology() {
  const S=state.data.matchup_settings||{};
  content.innerHTML=hero('{VU} · THE MODEL','From players to probabilities.','Sleeper player projections, a learned opponent-defense correction, and separately reported historical and live results.')+`<div class="prose"><h2>01 / Start with every projected player</h2><p>Sum Sleeper projected points across each full roster. Projected minutes validate team coverage between 200 and 280 minutes; they do not rescale the score. Empty roster placeholders remain missing and contribute no assumed points. Partial or inconsistent projected rows invalidate the snapshot. Sleeper does not supply a confirmed-starter flag, so game cards show the eight highest projected-minute players as the projected rotation.</p><div class="formula">H_raw = sum of home player points<br>A_raw = sum of away player points</div><h2>02 / Apply the {V} correction</h2><p>Fit historical team-point residuals with global, venue, offense and opponent-defense terms. Offense is an estimation control; it is not added to projected scores. Team effects use 25-calendar-day decay. Zero-centered ridge priors are ${S.defense_prior??40} fresh observations for defense, ${S.offense_prior??20} for offense and 100 each for global and venue.</p><p>Version 1.21 raised the defensive prior from 5 to 40. With about eight games per team, a prior of 5 moved margins by several points on noise; in last season's archive it scored worse than raw Sleeper through January and worse on win-probability log loss over the full season. A prior of 40 keeps the correction small until a team's defensive residual is established. This was a retrospective choice on previously inspected data, made in the direction of more shrinkage.</p><div class="formula">H = H_raw + global + venue / 2 + defense(away)<br>A = A_raw + global − venue / 2 + defense(home)<br>M = H − A &nbsp; · &nbsp; T = H + A</div><p>Raw scores remain visible. The first-stage team correction is initialized from 117 audited current-season legacy first snapshots. From v1.23 the correction applies from the first graded game of a season, locks use the same team correction as first snapshots, and it is refitted once each slate is fully graded. Earlier locks carried no correction. Seasons are kept separate.</p><h2>03 / Calibrate confidence prospectively</h2><p>Win-probability scale fits prior winner outcomes. ATS and totals learn uncertainty from errors against the exact frozen {V} forecasts. Confidence calibration uses a 75-completed-game half-life, persistent shrinkage, a 30-game minimum, daily movement limits and retention of accepted parameters on fit failure. Additional scalar ATS and totals biases remain zero because global and venue corrections are already included in scoring.</p><div class="formula">Home win probability = Φ(M / a)<br>Home cover probability = Φ((M + home spread) / ATS SD)<br>Over probability = Φ((T − market total) / total SD)</div><p>Initial values are a = 10, ATS SD = 14 and totals SD = 19. Φ is the standard normal cumulative distribution. These probability models are calibrated independently. Results must have been verified before each new forecast's calibration cutoff. From version 1.24 the confidence history starts with this season's earlier games, replayed under the current method from results known before each game, and adds completed native {V} forecasts; a native game replaces its replay. Lock forecasts use the same calibration as first snapshots. In a sequential test on this season's games this lowered log loss by 0.0019, an amount not distinguishable from zero.</p><h2>04 / Preserve a fixed control</h2><p>The fixed benchmark uses raw Sleeper scores, a win-probability scale of 10, ATS SD of 14 and totals SD of 19. Public comparisons use the same {V} live games and contemporaneous market opportunities for both variants.</p><h2>05 / Freeze the evidence</h2><p>The first valid pregame forecast is write-once. The lock is the first valid capture in the 15 minutes after T−5h, so every new lock has about the same lead time. A continuously running watcher wakes at each target. Locks frozen under the v1.20.1 policy were accepted until tip and keep that record; the Performance page reports scores by lead time so the two are not mixed. The Picks view falls back to the latest saved pregame snapshot, then the first snapshot, when no lock exists. Fallbacks are labeled and remain untracked. Latest previews are untracked and do not create bets or training observations. Major schedule changes retire the original instance and void its research selections.</p><h2>06 / Pick moneylines only</h2><p>ESPN supplies DraftKings two-sided prices. Quotes are timestamped when the response finishes; the sportsbook's own update time is unknown. Prices must satisfy pairing and pregame timing rules to create selections. The public pages display every model-predicted winner as a moneyline pick, regardless of price or edge. Historical research subsets remain in the audit ledger. One-unit returns use offered-price payouts and overtime-inclusive finals.</p><p>Spread and totals picks are retired. On this season's priced games the spread leans were an underdog bias produced by compressed margins, totals were a coin flip, and the market was more accurate at both. The model still forecasts margin and total, and the Performance page compares those forecasts with the market's lines, which is where an improvement would show first. Spread and totals selections recorded before v1.22 stay in the ledger.</p><p>The win-probability scale re-fits itself from completed games, which is the same adjustment as a slope on the margin. No separate slope is applied.</p><h2>07 / Pool the season, keep the parts visible</h2><p>The season record combines every first-snapshot game this season under {V}: archived first snapshots from before native capture began, earlier-version native captures re-scored with the current method, and live {V} forecasts. Replayed games are scored chronologically, using only results that were usable before each game, and their moneyline returns use the prices recorded at the time. Each part is listed beside the pooled line.</p><p>Replay is not the same evidence as live results. It is retrospective, the sample was inspected before {V} was set, and seed results use an assumed availability time. Replay creates no forecast, bet or grade in the ledger and trains nothing. Accuracy and probability scores are labeled exploratory below ${gates().forecast_games} graded games, and returns below ${gates().settled_bets} settled bets. Locks are live only and are not replayed. First and lock, adaptive and fixed, and moneyline results keep separate counts.</p><div class="notice">The watcher runs on GitHub Actions and hands off to a fresh run every few hours; a watchdog restarts it if the chain breaks. If it is down for a whole lock window, that lock is missed and shown in the coverage table. A delayed run cannot manufacture an earlier capture. No wagers are executed.</div><p><a class="green" href="MODEL.md">Read the complete build specification ↗</a> &nbsp; <a class="green" href="https://github.com/Dave356w/NBA-Sleeper-Model">Inspect the source and audit records ↗</a></p></div>`;
}
function render() {
  ({index:today,results,performance,methodology}[page] || today)();
  content.innerHTML=label(content.innerHTML); // One pass fills the version tokens before handlers attach.
  document.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>{state.stage=b.dataset.stage;if(page==='results'){const dates=[...new Set(state.data.forecasts.filter(f=>f.stage===state.stage).map(f=>f.game.slate_date))].sort();if(!dates.includes(state.date)&&dates.length)state.date=dates.at(-1);}render();});
  document.querySelectorAll('[data-variant]').forEach(b=>b.onclick=()=>{state.variant=b.dataset.variant;render();});
  const select=document.querySelector('#date-select');if(select) select.onchange=()=>{state.date=select.value;render();};
}
fetch('data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('Recorded data could not be loaded.');return r.json();}).then(data=>{
  if(!data.methodology||!data.methodology_version)throw new Error('Reporting data is not available yet.');
  METHODOLOGY=data.methodology; V='v'+data.methodology_version;
  data.forecasts=data.forecasts.filter(f=>f.methodology===METHODOLOGY);
  data.previews=(data.previews||[]).filter(f=>f.methodology===METHODOLOGY);
  data.performance=data.performance_by_methodology?.[METHODOLOGY]||data.performance;
  state.data=data;state.date=data.today;if(page==='index')state.stage='best';
  const dates=[...new Set((page==='results'?data.forecasts.filter(f=>f.stage===state.stage).map(f=>f.game.slate_date):data.games.map(g=>g.slate_date)))].sort();
  if(page==='results'){const settledDates=data.forecasts.filter(f=>f.stage===state.stage&&data.grades[f.id]&&!data.grades[f.id].void).map(f=>f.game.slate_date).sort();state.date=settledDates.at(-1)||dates.filter(d=>d<=data.today).at(-1)||dates[0]||data.today;}
  else if(!dates.includes(state.date)&&dates.length)state.date=dates.at(-1);
  document.querySelector('#updated').textContent=`Updated ${new Date(data.updated_at).toLocaleString()}`;
  document.querySelector('#section-title').textContent={index:"Today's slate",results:'Results',performance:'Performance',methodology:'Methodology'}[page];
  render();
}).catch(e=>{content.innerHTML=`<div class="error-display"><h2>Recorded data unavailable</h2><p>${esc(e.message)} Refresh later or use the forecast download.</p></div>`;});
