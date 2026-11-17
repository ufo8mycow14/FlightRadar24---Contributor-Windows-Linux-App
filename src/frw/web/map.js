/* Local aircraft overlays; only geographic background tiles go to OpenStreetMap. */
'use strict';
const FrwMap=(()=>{
  let map,tiles,antenna,onSelect,stationKey=null,centred=false,rows=[],selection='';
  const aircraft=new Map();
  const el=id=>document.getElementById(id);
  const positioned=row=>Number.isFinite(row.latitude)&&Number.isFinite(row.longitude)&&Math.abs(row.latitude)<=90&&Math.abs(row.longitude)<=180;
  function init(select){
    onSelect=select;
    map=L.map('airspace-map',{zoomControl:true,scrollWheelZoom:false,minZoom:2,maxZoom:18,worldCopyJump:true}).setView([0,0],2);
    L.control.scale({imperial:true,metric:true}).addTo(map);
    tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin',
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(map);
    let tileFailures=0;
    tiles.on('loading',()=>{tileFailures=0;});
    tiles.on('tileerror',()=>{tileFailures++;el('map-tile-status').textContent='Map tiles unavailable. Aircraft positions still update; use the range radar below for an offline view.';el('map-tile-status').hidden=false;});
    tiles.on('load',()=>{if(!tileFailures)el('map-tile-status').hidden=true;});
    el('map-home').onclick=()=>{if(antenna)map.setView(antenna.getLatLng(),9);};
    el('map-fit').onclick=fit;
    map.on('moveend',projectLayers);
    new ResizeObserver(resize).observe(el('airspace-map'));
  }
  function icon(row,chosen){
    const content=document.createElement('div');content.className='aircraft-marker-content';
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 36 36');svg.setAttribute('aria-hidden','true');
    const shape=document.createElementNS(svg.namespaceURI,'path');
    shape.setAttribute('d','M18 2 21 14 32 22 32 25 21 22 20 29 24 32 24 34 18 32 12 34 12 32 16 29 15 22 4 25 4 22 15 14Z');
    shape.setAttribute('transform',`rotate(${Number.isFinite(row.track)?row.track:0} 18 18)`);
    svg.append(shape);content.append(svg);
    const label=document.createElement('span');label.className='aircraft-map-label';label.textContent=(row.callsign||row.icao).trim();content.append(label);
    return L.divIcon({html:content,className:'aircraft-map-icon'+(chosen?' is-selected':''),iconSize:[36,36],iconAnchor:[18,18],popupAnchor:[0,-18]});
  }
  function update(next,config,selected){
    if(!map)return;
    rows=next.filter(positioned);selection=selected;
    const station=config&&Number.isFinite(config.latitude)&&Number.isFinite(config.longitude)?[config.latitude,config.longitude]:null;
    const key=station?station.join(','):'';
    if(key!==stationKey){
      if(antenna){antenna.remove();antenna=null;}
      if(station){
        antenna=L.circleMarker(station,{radius:6,color:'#ffffff',weight:2,fillColor:'#087768',fillOpacity:1}).addTo(map).bindTooltip('Your antenna');
        map.setView(station,9);centred=true;
      }
      stationKey=key;
    }
    if(!centred&&rows.length){map.setView([rows[0].latitude,rows[0].longitude],9);centred=true;}
    el('map-home').disabled=!antenna;el('map-fit').disabled=!rows.length;
    el('map-summary').textContent=`${rows.length} aircraft with a position${next.length>rows.length?` · ${next.length-rows.length} awaiting coordinates`:''}`;
    el('map-empty').hidden=rows.length>0;
    const present=new Set(rows.map(row=>row.icao));
    for(const [icao,entry] of aircraft){if(!present.has(icao)){entry.marker.remove();entry.trail.remove();aircraft.delete(icao);}}
    for(const row of rows){
      const chosen=row.icao===selection;
      let entry=aircraft.get(row.icao);
      if(!entry){
        const marker=L.marker([row.latitude,row.longitude],{icon:icon(row,chosen),keyboard:true,title:`Select aircraft ${row.icao}`,alt:`Aircraft ${row.icao}`,riseOnHover:true}).addTo(map);
        marker.on('click',()=>{onSelect(row.icao);focus(row.icao);});
        const trail=L.polyline([],{color:'#087768',weight:2,opacity:.65,interactive:false}).addTo(map);
        entry={marker,trail,iconKey:''};aircraft.set(row.icao,entry);
      }
      const iconKey=[row.callsign,row.track,chosen].join('|');
      if(iconKey!==entry.iconKey){entry.marker.setIcon(icon(row,chosen));entry.iconKey=iconKey;}
      entry.marker.setZIndexOffset(chosen?1000:0);
      entry.trail.setStyle({color:chosen?'#c27900':'#087768',weight:chosen?3:2,opacity:chosen?.95:.55});
      if(chosen)entry.trail.bringToFront();
    }
    projectLayers();
  }
  function projectLayers(){
    if(!map)return;
    const centre=map.getCenter().lng;
    const nearby=(longitude,reference)=>longitude+360*Math.round((reference-longitude)/360);
    for(const row of rows){
      const entry=aircraft.get(row.icao);if(!entry)continue;
      const longitude=nearby(row.longitude,centre);
      entry.marker.setLatLng([row.latitude,longitude]);
      const trail=(row.trail||[]).filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1])).map(point=>[point[0],point[1]]);
      if(trail.length)trail.push([row.latitude,row.longitude]);
      // Work backwards from the current marker to keep tracks continuous at the date line.
      let reference=longitude;
      for(let i=trail.length-1;i>=0;i--){trail[i][1]=nearby(trail[i][1],reference);reference=trail[i][1];}
      entry.trail.setLatLngs(trail);
    }
    if(antenna&&stationKey){const [latitude,longitude]=stationKey.split(',').map(Number);antenna.setLatLng([latitude,nearby(longitude,centre)]);}
  }
  function focus(icao){
    const entry=aircraft.get(icao);if(!entry)return;
    requestAnimationFrame(()=>{map.invalidateSize({pan:true,animate:false});map.panTo(entry.marker.getLatLng());});
  }
  function fit(){
    if(!rows.length)return;
    const longitudes=rows.map(row=>(row.longitude+360)%360).sort((a,b)=>a-b);
    let gap=-1,start=longitudes[0];
    for(let i=0;i<longitudes.length;i++){const next=longitudes[(i+1)%longitudes.length]+(i===longitudes.length-1?360:0);if(next-longitudes[i]>gap){gap=next-longitudes[i];start=next%360;}}
    const points=rows.map(row=>{let longitude=(row.longitude+360)%360;if(longitude<start)longitude+=360;return [row.latitude,longitude];});
    map.closePopup();map.fitBounds(points,{padding:[55,55],maxZoom:rows.length===1?9:11});
  }
  function resize(){if(map&&el('airspace-map').offsetWidth&&el('airspace-map').offsetHeight)requestAnimationFrame(()=>map.invalidateSize({pan:true,animate:false,debounceMoveend:true}));}
  return {init,update,focus,resize};
})();
