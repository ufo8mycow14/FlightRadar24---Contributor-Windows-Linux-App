/* Selected-aircraft telemetry from the local receiver. No metadata service. */
'use strict';
const AircraftDetails=(()=>{
  const byId=id=>document.getElementById(id);
  const fields=new Map();
  let selected='',snapshot=null,history=[],input='',lost=false,receivedAt=0,generation=0,inFlight=0;
  let loadDetail,onClose,restoreFocus;
  let detailError='',connectionFailed=false;
  const present=value=>value!==undefined&&value!==null&&value!=='';
  const number=(value,unit='',digits=0)=>Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits})+(unit?' '+unit:''):'Unavailable';
  const ageText=seconds=>seconds<60?Math.round(seconds)+'s ago':seconds<3600?Math.floor(seconds/60)+'m '+Math.round(seconds%60)+'s ago':Math.floor(seconds/3600)+'h ago';
  const emergencyNames=['No emergency reported','General emergency','Medical / lifeguard','Minimum fuel','No communications','Unlawful interference','Downed aircraft','Reserved'];
  const groups=[
    ['Identity & status',false,[
      ['icao','ICAO24 address','text'],['squawk','Squawk','text'],['wake_vortex','Emitter category','text'],['category','Category code','category'],
      ['on_ground','Flight state','ground'],['emergency_state','Emergency status','emergency']
    ]],
    ['Position',false,[
      ['latitude','Latitude','coordinate'],['longitude','Longitude','coordinate'],['distance_nm','Distance from antenna','distance']
    ]],
    ['Flight measurements',false,[
      ['altitude_baro','Barometric altitude','feet'],['altitude_gnss','GNSS altitude','feet'],['altitude','Reported altitude','feet'],
      ['groundspeed','Ground speed','knots'],['indicated_airspeed','Indicated airspeed','knots'],['true_airspeed','True airspeed','knots'],['mach','Mach','mach'],
      ['track','Ground track','degrees'],['true_track','True track','degrees'],['heading','Heading','heading'],['magnetic_heading','Magnetic heading','degrees'],
      ['vertical_rate','Vertical rate','vertical'],['baro_vertical_rate','Barometric vertical rate','vertical'],['gnss_vertical_rate','GNSS vertical rate','vertical'],['inertial_vertical_rate','Inertial vertical rate','vertical'],
      ['geo_minus_baro','GNSS minus barometric altitude','feet'],['roll','Roll angle','degrees'],['track_rate','Track rate','turn']
    ]],
    ['Reported targets & modes',true,[
      ['selected_altitude','Selected altitude','feet'],['selected_altitude_source','Selected altitude source','text'],['selected_altitude_mcp','MCP selected altitude','feet'],['selected_altitude_fms','FMS selected altitude','feet'],['target_altitude_source','Target altitude source','text'],
      ['selected_heading','Selected heading','degrees'],['baro_pressure_setting','Pressure setting','pressure'],['autopilot','Autopilot','boolean'],['vnav_mode','VNAV','boolean'],['lnav_mode','LNAV','boolean'],['altitude_hold_mode','Altitude hold','boolean'],['approach_mode','Approach mode','boolean']
    ]],
    ['Signal integrity & capability',true,[
      ['version','ADS-B version','code'],['nac_p','Position accuracy (NACp)','quality'],['nac_v','Velocity accuracy (NACv)','quality'],['nuc_p','Legacy NUCp code (from typecode)','quality'],['sil','Source integrity (SIL)','quality'],
      ['sil_supplement','SIL supplement','code'],['nic_b','NIC B / SAF bit','code'],['nic_supplement_a','NIC supplement A','code'],['nic_baro','Barometric integrity bit','code'],['hrd','Heading reference','reference'],['capability_class','Capability bitfield','hex'],['operational_mode','Operational bitfield','hex'],
      ['tcas_operational','TCAS operational','boolean'],['acas_operational','ACAS operational','boolean'],['issued_ra','Resolution advisory issued','boolean'],['ra_terminated','Resolution advisory terminated','boolean'],['multiple_threat','Multiple threats','boolean']
    ]]
  ];
  function init(loader,close){
    loadDetail=loader;onClose=close;
    const root=byId('detail-fields');
    for(const [title,collapsed,descriptors] of groups){
      const section=document.createElement(collapsed?'details':'section');section.className='detail-section';
      const heading=document.createElement(collapsed?'summary':'h3');heading.textContent=title;section.append(heading);
      if(title==='Signal integrity & capability'){const note=document.createElement('p');note.className='detail-help';note.textContent='Broadcast codes and flags; these are not signal strength or reception percentages.';section.append(note);}
      const list=document.createElement('dl');list.className='detail-data';
      for(const [key,label,format] of descriptors){
        const row=document.createElement('div');row.className='detail-data-row';row.dataset.detailField=key;
        const term=document.createElement('dt');term.textContent=label;
        const definition=document.createElement('dd'),value=document.createElement('span'),age=document.createElement('small');
        value.className='detail-value';age.className='detail-field-age';definition.append(value,age);row.append(term,definition);list.append(row);
        fields.set(key,{row,value,age,format});
      }
      section.append(list);root.append(section);
      if(title==='Position')root.append(byId('detail-history-panel'));
    }
    const extra=document.createElement('details');extra.className='detail-section';
    const summary=document.createElement('summary');summary.textContent='Additional received data';
    const list=document.createElement('dl');list.className='detail-data';list.id='detail-extra-fields';extra.append(summary,list);root.append(extra);
    byId('details-close').onclick=()=>closePanel(true);
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&selected){event.preventDefault();closePanel(true);}});
    new ResizeObserver(()=>{if(selected)drawHistory();}).observe(byId('aircraft-details'));
  }
  function select(icao,row,source){
    const changed=selected!==icao;
    if(changed){generation++;inFlight=0;snapshot=null;history=[];restoreFocus=document.activeElement;byId('detail-scroll').scrollTop=0;}
    selected=icao;input=source;lost=!row;detailError='';connectionFailed=false;
    if(row){snapshot=row;receivedAt=Date.now()/1000;}
    byId('aircraft-details').hidden=false;document.body.classList.add('details-open');
    render();
    if(changed)byId('details-close').focus({preventScroll:true});
    refresh();
  }
  function closePanel(returnFocus=false){
    if(!selected)return;
    const previous=selected;
    selected='';generation++;inFlight=0;byId('aircraft-details').hidden=true;document.body.classList.remove('details-open');
    onClose();
    if(returnFocus){
      const target=restoreFocus?.isConnected?restoreFocus:[...document.querySelectorAll('.icao')].find(node=>node.textContent===previous)||byId('airspace-map');
      target.focus({preventScroll:true});
    }
  }
  function update(rows,source){
    if(!selected)return;
    input=source;connectionFailed=false;const row=rows.find(item=>item.icao===selected);lost=!row;
    if(row){snapshot=row;receivedAt=Date.now()/1000;}
    render();
  }
  async function refresh(){
    if(!selected||inFlight===generation+1)return;
    const id=selected,requestGeneration=generation;inFlight=generation+1;
    try{
      const detail=await loadDetail(id);
      if(selected!==id||generation!==requestGeneration)return;
      snapshot=detail;history=Array.isArray(detail.history)?detail.history.slice(-300):[];receivedAt=Date.now()/1000;lost=false;detailError='';render();
    }catch(error){
      if(selected!==id||generation!==requestGeneration)return;
      if(error.status===404){lost=true;detailError='';}
      else detailError='Details temporarily unavailable. Receiver status continues to update when connected.';
      render();
    }finally{if(generation===requestGeneration)inFlight=0;}
  }
  function connectionError(){if(selected){connectionFailed=true;render();}}
  function sourceLabel(){
    const source=snapshot?.data_source||'Source not reported';
    if(input==='iq-file')return 'Recorded '+source;
    if(input==='usb')return source==='ADS-B'?'Terrestrial ADS-B':'Terrestrial '+source;
    const receiver={'beast-tcp':'Beast TCP','beast-serial':'Beast serial','avr-tcp':'AVR TCP','avr-serial':'AVR serial','sbs-tcp':'SBS TCP'}[input];
    return receiver?source+' via '+receiver:source;
  }
  function fieldAge(key){
    const time=snapshot?.field_times?.[key];
    if(Number.isFinite(time))return Math.max(0,Date.now()/1000-time);
    const age=snapshot?.field_ages?.[key];
    if(Number.isFinite(age))return Math.max(0,age+Date.now()/1000-receivedAt);
    if(['latitude','longitude','distance_nm'].includes(key)&&Number.isFinite(snapshot?.position_time))return Math.max(0,Date.now()/1000-snapshot.position_time);
    return null;
  }
  function display(key,value,format){
    if(!present(value))return 'Not received';
    switch(format){
      case 'feet':return number(value,'ft');case 'knots':return number(value,'kt',1);case 'degrees':return number(value,'°',1);case 'distance':return number(value,'NM',1);
      case 'coordinate':return Number.isFinite(value)?value.toFixed(6):'Unavailable';case 'vertical':return number(value,'ft/min');case 'pressure':return number(value,'hPa',1);case 'turn':return number(value,'°/s',2);
      case 'mach':return Number.isFinite(value)?value.toFixed(3):'Unavailable';case 'boolean':return value?'On / yes':'Off / no';case 'ground':return value?'On ground':'Airborne';
      case 'emergency':return emergencyNames[value]||'Code '+value;case 'reference':return value===0?'True north':value===1?'Magnetic north':'Code '+value;
      case 'quality':return value===0?'0 · unknown / unavailable':'Code '+value;case 'code':return String(value);case 'hex':return Number.isInteger(value)?'0x'+value.toString(16).toUpperCase():'Unavailable';
      case 'category':return String(value)+(present(snapshot.category_typecode)?' · typecode '+snapshot.category_typecode:'');
      case 'heading':return number(value,'°',1)+(snapshot.hrd===0?' true':snapshot.hrd===1?' magnetic':'');
      default:return String(value);
    }
  }
  function render(){
    if(!selected)return;
    const row=snapshot||{icao:selected};
    byId('detail-callsign').textContent=present(row.callsign)?String(row.callsign).trim():selected;
    byId('detail-subtitle').textContent=present(row.callsign)?'ICAO24 '+selected:'Callsign not received';
    byId('detail-source').textContent=sourceLabel();
    const seen=Number.isFinite(row.last_seen)?Math.max(0,Date.now()/1000-row.last_seen):Number.isFinite(row.seen)?row.seen+Date.now()/1000-receivedAt:null;
    byId('detail-last-seen').textContent=seen===null?'Waiting for a report':'Last received '+ageText(seen);
    byId('detail-reception').textContent=connectionFailed?'Connection interrupted':lost?'Signal lost':input==='iq-file'?'Recorded reception':seen>15?'Reception quiet':'Receiving';
    byId('detail-reception').className='detail-reception'+(lost||connectionFailed?' is-lost':'');
    byId('detail-lost').hidden=!lost;
    byId('detail-fetch-error').textContent=connectionFailed?'The local receiver connection is interrupted. Showing the last received values.':detailError;
    byId('detail-fetch-error').hidden=!connectionFailed&&!detailError;
    byId('detail-message-count').textContent=Number.isFinite(row.messages)?number(row.messages)+' messages received':'Waiting for messages';
    const ignored=new Set(['icao','messages','first_seen','last_seen','seen','trail','history','position_time','field_times','field_ages','field_sources','data_source','callsign','altitude_source','vr_source','category_typecode']);
    const labels={df:'Downlink format (DF)',typecode:'ADS-B typecode',subtype:'Message subtype',bds:'Decoded BDS register',bds_candidates:'Ambiguous BDS candidates',supported_bds:'Supported BDS registers',altitude_unspecified:'Altitude (source unspecified)',vertical_rate_unspecified:'Vertical rate (source unspecified)'};
    for(const key of new Set([...Object.keys(row),...Object.keys(row.field_times||{})])){
      if(fields.has(key)||ignored.has(key))continue;
      const value=row[key];if(present(value)&&typeof value==='object'&&!Array.isArray(value))continue;
      const item=document.createElement('div');item.className='detail-data-row';item.dataset.detailField=key;
      const term=document.createElement('dt');term.textContent=labels[key]||key.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());
      const definition=document.createElement('dd'),valueNode=document.createElement('span'),age=document.createElement('small');valueNode.className='detail-value';age.className='detail-field-age';definition.append(valueNode,age);item.append(term,definition);byId('detail-extra-fields').append(item);
      fields.set(key,{row:item,value:valueNode,age,format:typeof value==='boolean'?'boolean':'text'});
    }
    for(const [key,entry] of fields){
      const value=key==='icao'?selected:row[key],age=fieldAge(key),hasValue=present(value);
      const stale=lost||connectionFailed||(age!==null&&!hasValue);
      entry.row.classList.toggle('is-stale',stale);entry.row.classList.toggle('is-unavailable',!hasValue);
      entry.value.textContent=hasValue?display(key,value,entry.format):age!==null?'Stale · unavailable':'Not received';
      let note=age===null?'':ageText(age);
      if((lost||connectionFailed)&&key!=='icao')note='Last known'+(note?' · '+note:'');
      if(key==='altitude'&&row.altitude_source)note+=(note?' · ':'')+row.altitude_source;
      if(key==='vertical_rate'&&row.vr_source)note+=(note?' · ':'')+row.vr_source;
      entry.age.textContent=note;
      const provenance=row.field_sources?.[key];entry.row.title=provenance?'Received via '+provenance:'';
    }
    const altitude=present(row.altitude_baro)?row.altitude_baro:row.altitude;
    byId('detail-altitude-value').textContent=present(altitude)?number(altitude,'ft'):'Not received';
    byId('detail-altitude-label').textContent=present(row.altitude_baro)?'BAROMETRIC ALTITUDE':'REPORTED ALTITUDE';
    byId('detail-speed-value').textContent=present(row.groundspeed)?number(row.groundspeed,'kt',1):'Not received';
    byId('detail-summary-values').classList.toggle('is-stale',lost||connectionFailed);
    drawHistory();
  }
  function drawHistory(){
    if(!selected)return;
    drawChart('altitude-history',[['altitude_baro','Barometric','#08776a'],['altitude_gnss','GNSS','#9f5bca'],['altitude','Unspecified','#647b8d']], 'ft');
    drawChart('speed-history',[['groundspeed','Ground','#08776a'],['indicated_airspeed','Indicated','#bf731d'],['true_airspeed','True','#526cc5']], 'kt');
  }
  function drawChart(id,series,unit){
    const svg=byId(id),ns='http://www.w3.org/2000/svg',width=340,height=135,left=46,right=9,top=12,bottom=25;
    svg.replaceChildren();svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const now=Date.now()/1000,start=now-600;
    const samples=history.filter(sample=>Number.isFinite(sample.time)&&sample.time>=start-2&&sample.time<=now+2);
    const all=series.flatMap(([key])=>samples.filter(sample=>Number.isFinite(sample[key])&&(key!=='altitude'||(!Number.isFinite(sample.altitude_baro)&&!Number.isFinite(sample.altitude_gnss)))).map(sample=>sample[key]));
    const append=(tag,attrs,text)=>{const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);if(text!==undefined)node.textContent=text;svg.append(node);return node;};
    if(!all.length){append('text',{x:width/2,y:height/2,'text-anchor':'middle',class:'chart-empty'},'Waiting for received '+(unit==='ft'?'altitude':'speed')+' samples');byId(id+'-legend').textContent='No samples yet';return;}
    let min=Math.min(...all),max=Math.max(...all);if(min===max){const margin=unit==='ft'?100:10;min-=margin;max+=margin;}
    const x=time=>left+(time-start)/600*(width-left-right),y=value=>top+(max-value)/(max-min)*(height-top-bottom);
    for(let i=0;i<3;i++){const value=min+(max-min)*i/2;append('line',{x1:left,y1:y(value),x2:width-right,y2:y(value),class:'chart-grid'});append('text',{x:left-5,y:y(value)+3,'text-anchor':'end',class:'chart-axis'},number(value));}
    append('text',{x:left,y:height-5,class:'chart-axis'},'10 min ago');append('text',{x:width-right,y:height-5,'text-anchor':'end',class:'chart-axis'},'Now');
    const active=[];
    for(const [key,label,color] of series){
      const points=samples.filter(sample=>Number.isFinite(sample[key])&&(key!=='altitude'||(!Number.isFinite(sample.altitude_baro)&&!Number.isFinite(sample.altitude_gnss)))).map(sample=>({time:sample.field_times?.[key]??sample.time,value:sample[key]})).filter(sample=>sample.time>=start&&sample.time<=now+2);
      if(!points.length)continue;active.push(label+' ('+unit+')');
      let path='',previous=null;
      for(const sample of points){path+=(previous&&sample.time-previous.time<=30?' L':' M')+x(sample.time).toFixed(1)+' '+y(sample.value).toFixed(1);append('circle',{cx:x(sample.time),cy:y(sample.value),r:1.7,fill:color});previous=sample;}
      append('path',{d:path,fill:'none',stroke:color,'stroke-width':1.8});
    }
    byId(id+'-legend').textContent=active.join(' · ');
  }
  return {init,select,update,refresh,connectionError,close:closePanel};
})();
