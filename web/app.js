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
function performance() {
  const season=state.data.season;
  const head=hero('{VU} · PERFORMANCE','Model versus market.','The {V} season record against final scores and saved DraftKings prices.');
  if(!season?.pooled?.n){content.innerHTML=head+empty('No results yet','Performance appears once the first games are final.');return;}
  const p=season.pooled, b=p.betting;
  const defaults={wp_a:10,ats_bias:0,ats_sigma:14,totals_bias:0,totals_sigma:19};
  const param=state.data.calibrations?.first?.params||defaults;
  const series=(name,m)=>`<tr><td>${name}</td><td>${m.n}</td><td>${m.n?`${m.wins}–${m.losses}`:'—'}</td><td>${m.n?signed(m.units,2)+'u':'—'}</td><td>${pct(m.roi)}</td></tr>`;
  content.innerHTML=head+`<section aria-labelledby="season-title"><div class="toolbar filters-first"><h2 id="season-title">${esc(season.season)} season record</h2></div>${seasonStats(p)}<div class="stats">${stat('Log loss',num(p.log_loss,4),'Win probability · lower is better')}${stat('Margin error',num(p.margin_mae,2),'Mean absolute error · points')}${stat('Total error',num(p.total_mae,2),'Mean absolute error · points')}</div></section>${versusMarket(p.versus_market)}<section class="panel"><h2>Moneyline returns</h2><p class="section-note">One unit per bet at the saved DraftKings price. The value series needs a win probability at least five points above the market’s and a positive expected return.</p><div class="table-wrap"><table><thead><tr><th>Series</th><th>Bets</th><th>W–L</th><th>Net units</th><th>ROI</th></tr></thead><tbody>${series('Model winner',b.directional)}${series('Value',b.value)}</tbody></table></div></section>${favoriteBenchmark(p.favorite_benchmark)}<details class="technical-details" id="model-details"><summary>Model details</summary><h2>Parameters in use</h2><div class="parameter-grid">${[['WP scale',param.wp_a],['ATS bias',param.ats_bias],['ATS SD',param.ats_sigma],['Total bias',param.totals_bias],['Total SD',param.totals_sigma]].map(([name,value])=>stat(name,num(value),'points')).join('')}</div>${coverageTable()}${defenseTable()}</details>`;
}
function coverageTable() {
  const rows=(state.data.lock_coverage||[]).slice(-10).reverse(); if(!rows.length)return '';
  const locked=rows.reduce((a,r)=>a+r.locked,0), games=rows.reduce((a,r)=>a+r.games,0);
  return `<h2 style="margin-top:30px">Lock capture coverage</h2><p class="small">${locked} of ${games} games locked across the last ${rows.length} closed slates, all versions. A lock is missed when no valid capture lands in its window (15 minutes after T−5h; through tip for locks frozen under v1.20.1); missed games have no lock record and are never backfilled.</p><div class="table-wrap"><table><thead><tr><th>Slate</th><th>Locked</th><th>Missed</th><th>Median lead</th><th>Shortest lead</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(r.slate_date)}</td><td>${r.locked} / ${r.games}</td><td>${r.missed}</td><td>${r.median_lead_hours===null?'—':num(r.median_lead_hours,1)+'h'}</td><td>${r.min_lead_hours===null?'—':num(r.min_lead_hours,1)+'h'}</td></tr>`).join('')}</tbody></table></div>`;
}
function defenseTable() {
  const v=state.data.matchup_calibrations?.first, S=state.data.matchup_settings; if(!v||!S)return '';
  const teams=Object.keys(v.params.defense).sort((a,b)=>v.params.defense[a]-v.params.defense[b]);
  return `<h2 style="margin-top:30px">Residual defense adjustments</h2><p class="small">${S.half_life_days}-calendar-day decay · zero-centered defensive prior: ${S.defense_prior} games · offense control: ${S.offense_prior} games. Negative values reduce opponent scoring. Fitted on ${v.training_count||0} games.</p><div class="table-wrap"><table><thead><tr><th>Team</th><th>Training games</th><th>Opponent points adjustment</th></tr></thead><tbody>${teams.map(t=>`<tr><td>${esc(t)}</td><td>${v.team_counts?.[t]||0}</td><td>${signed(v.params.defense[t])}</td></tr>`).join('')}</tbody></table></div>`;
}
function methodology() {
  const S=state.data.matchup_settings||{};
  content.innerHTML=hero('{VU} · THE MODEL','From players to probabilities.','Sleeper player projections, a learned opponent-defense correction, and separately reported historical and live results.')+`<div class="prose"><h2>01 / Start with every projected player</h2><p>Sum Sleeper projected points across each full roster. Projected minutes validate team coverage between 200 and 280 minutes; they do not rescale the score. Empty roster placeholders remain missing and contribute no assumed points. Partial or inconsistent projected rows invalidate the snapshot. Sleeper does not supply a confirmed-starter flag, so game cards show the eight highest projected-minute players as the projected rotation.</p><div class="formula">H_raw = sum of home player points<br>A_raw = sum of away player points</div><h2>02 / Apply the {V} correction</h2><p>Fit historical team-point residuals with global, venue, offense and opponent-defense terms. Offense is an estimation control; it is not added to projected scores. Team effects use 25-calendar-day decay. Zero-centered ridge priors are ${S.defense_prior??40} fresh observations for defense, ${S.offense_prior??20} for offense and 100 each for global and venue.</p><p>Version 1.21 raised the defensive prior from 5 to 40. With about eight games per team, a prior of 5 moved margins by several points on noise; in last season's archive it scored worse than raw Sleeper through January and worse on win-probability log loss over the full season. A prior of 40 keeps the correction small until a team's defensive residual is established. This was a retrospective choice on previously inspected data, made in the direction of more shrinkage.</p><div class="formula">H = H_raw + global + venue / 2 + defense(away)<br>A = A_raw + global − venue / 2 + defense(home)<br>M = H − A &nbsp; · &nbsp; T = H + A</div><p>Raw scores remain visible. The first-stage team correction is initialized from 117 audited current-season legacy first snapshots. From v1.23 the correction applies from the first graded game of a season, locks use the same team correction as first snapshots, and it is refitted once each slate is fully graded. Earlier locks carried no correction. Seasons are kept separate.</p><h2>03 / Calibrate confidence prospectively</h2><p>Win-probability scale fits prior winner outcomes. ATS and totals learn uncertainty from errors against the exact frozen {V} forecasts. Confidence calibration uses a 75-completed-game half-life, persistent shrinkage, a 30-game minimum, daily movement limits and retention of accepted parameters on fit failure. Additional scalar ATS and totals biases remain zero because global and venue corrections are already included in scoring.</p><div class="formula">Home win probability = Φ(M / a)<br>Home cover probability = Φ((M + home spread) / ATS SD)<br>Over probability = Φ((T − market total) / total SD)</div><p>Initial values are a = 10, ATS SD = 14 and totals SD = 19. Φ is the standard normal cumulative distribution. These probability models are calibrated independently. Results must have been verified before each new forecast's calibration cutoff. From version 1.24 the confidence history starts with this season's earlier games, replayed under the current method from results known before each game, and adds completed native {V} forecasts; a native game replaces its replay. Lock forecasts use the same calibration as first snapshots. In a sequential test on this season's games this lowered log loss by 0.0019, an amount not distinguishable from zero.</p><h2>04 / Preserve a fixed control</h2><p>The fixed benchmark uses raw Sleeper scores, a win-probability scale of 10, ATS SD of 14 and totals SD of 19. Public comparisons use the same {V} live games and contemporaneous market opportunities for both variants.</p><h2>05 / Freeze the evidence</h2><p>The first valid pregame forecast is write-once. The lock is the first valid capture in the 15 minutes after T−5h, so every new lock has about the same lead time. A continuously running watcher wakes at each target. Locks frozen under the v1.20.1 policy were accepted until tip and keep that record; The Picks view falls back to the latest saved pregame snapshot, then the first snapshot, when no lock exists. Fallbacks are labeled and remain untracked. Latest previews are untracked and do not create bets or training observations. Major schedule changes retire the original instance and void its research selections.</p><h2>06 / Pick moneylines only</h2><p>ESPN supplies DraftKings two-sided prices. Quotes are timestamped when the response finishes; the sportsbook's own update time is unknown. Prices must satisfy pairing and pregame timing rules to create selections. The public pages display every model-predicted winner as a moneyline pick, regardless of price or edge. Historical research subsets remain in the audit ledger. One-unit returns use offered-price payouts and overtime-inclusive finals.</p><p>Spread and totals picks are retired. On this season's priced games the spread leans were an underdog bias produced by compressed margins, totals were a coin flip, and the market was more accurate at both. The model still forecasts margin and total, and the Performance page compares those forecasts with the market's lines, which is where an improvement would show first. Spread and totals selections recorded before v1.22 stay in the ledger.</p><p>The win-probability scale re-fits itself from completed games, which is the same adjustment as a slope on the margin. No separate slope is applied.</p><h2>07 / One season record</h2><p>Results and Performance show one season record under {V}: every first-snapshot game this season. Games forecast live under {V} count as frozen. Earlier games (archived first snapshots from before native capture began, and captures made under earlier versions) are replayed with the current method, chronologically, using only results that were known before each game, with the moneyline prices saved at the time. A game appears once.</p><p>Replayed games are scored after the fact and their result times are partly assumed; they also start the confidence history described above. Replay writes no forecast, pick or grade to the ledger. Locks are not replayed.</p><h2>08 / Live scores and win chance</h2><p>While a game is in progress, Today shows its score and clock from ESPN, read in your browser every 30 seconds, and a live win chance computed from the score, the time left and the DraftKings spread saved with the forecast: Φ(a·lead/√τ + b·μ·√τ), where μ is the pregame expected home margin and τ the share of regulation left. Its coefficients were fitted on play-by-play from 2015-16 to 2025-26. It is a display only: separate from the pregame forecast, never saved, and never part of a pick, grade or record. It is absent when no spread was saved.</p><div class="notice">The watcher runs on GitHub Actions and hands off to a fresh run every few hours; a watchdog restarts it if the chain breaks. If it is down for a whole lock window, that lock is missed and counted in the coverage table. A delayed run cannot manufacture an earlier capture. No wagers are executed.</div><p><a class="green" href="MODEL.md">Read the complete build specification ↗</a> &nbsp; <a class="green" href="https://github.com/Dave356w/NBA-Sleeper-Model">Inspect the source and audit records ↗</a></p></div>`;
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
