/* Contururi salvate ca GeoJSON; datele OSM nu sunt incluse în proiect. */
(() => {
  const el = id => document.getElementById(id);
  const storageKey = 'terragen-drawn-zones-v1';
  const empty = () => ({type:'FeatureCollection',features:[]});
  let zones = [], points = [], drawing = false, closed = false;
  const map = new maplibregl.Map({container:'map', center:[24.5612,46.5446], zoom:14,
    pitch:0, bearing:0, maxPitch:0, attributionControl:true,
    style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize:256,maxzoom:19,attribution:'© OpenStreetMap contributors'}},layers:[{id:'osm',type:'raster',source:'osm'}]}});
  map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-left');
  map.doubleClickZoom.disable();
  function message(text){el('status').textContent=text;}
  function persist(){try{localStorage.setItem(storageKey,JSON.stringify(zones));return true;}catch(e){message('Nu am putut salva în browser. Descarcă proiectul.');return false;}}
  function ringValid(ring){
    if(!Array.isArray(ring)||ring.length<4||ring.length>501)return false;
    if(ring.some(p=>!Array.isArray(p)||p.length!==2||!p.every(Number.isFinite)||Math.abs(p[0])>180||Math.abs(p[1])>85))return false;
    if(ring[0][0]!==ring[ring.length-1][0]||ring[0][1]!==ring[ring.length-1][1])return false;
    let area=0;
    for(let i=0;i<ring.length-1;i++){
      const a=ring[i],b=ring[i+1];if(a[0]===b[0]&&a[1]===b[1])return false;
      area+=a[0]*b[1]-b[0]*a[1];
    }
    if(Math.abs(area)<1e-12)return false;
    const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    const on=(a,b,p)=>Math.abs(cross(a,b,p))<1e-12&&p[0]>=Math.min(a[0],b[0])-1e-12&&p[0]<=Math.max(a[0],b[0])+1e-12&&p[1]>=Math.min(a[1],b[1])-1e-12&&p[1]<=Math.max(a[1],b[1])+1e-12;
    const intersects=(a,b,c,d)=>{const x=cross(a,b,c),y=cross(a,b,d),z=cross(c,d,a),w=cross(c,d,b);
      return (x*y<0&&z*w<0)||on(a,b,c)||on(a,b,d)||on(c,d,a)||on(c,d,b);};
    const count=ring.length-1;
    for(let i=0;i<count;i++)for(let j=i+1;j<count;j++){
      if(j===i+1||(i===0&&j===count-1))continue;
      if(intersects(ring[i],ring[i+1],ring[j],ring[j+1]))return false;
    }
    return true;
  }
  function goodFeature(f){return f&&f.type==='Feature'&&f.geometry&&f.geometry.type==='Polygon'&&
    Array.isArray(f.geometry.coordinates)&&f.geometry.coordinates.length===1&&ringValid(f.geometry.coordinates[0])&&
    f.properties&&typeof f.properties.name==='string'&&f.properties.name.trim().length>0&&f.properties.name.length<=80;}
  function controls(){el('start').disabled=drawing||closed;el('undo').disabled=!drawing||points.length===0;
    el('finish').disabled=!drawing||points.length<3;el('cancel').disabled=!drawing&&!closed;el('save').disabled=!closed;}
  function update(){
    if(!map.getSource('saved'))return;
    map.getSource('saved').setData({type:'FeatureCollection',features:zones});
    const features=[];
    if(points.length>=2)features.push({type:'Feature',properties:{},geometry:{type:'LineString',coordinates:closed?[...points,points[0]]:points}});
    for(const p of points)features.push({type:'Feature',properties:{},geometry:{type:'Point',coordinates:p}});
    map.getSource('draft').setData({type:'FeatureCollection',features});controls();
  }
  function list(){
    const container=el('zones');container.replaceChildren();
    if(!zones.length){container.textContent='Nicio zonă salvată.';return;}
    zones.forEach((zone,i)=>{const box=document.createElement('div');box.className='zone';
      const title=document.createElement('strong');title.textContent=zone.properties.name;box.appendChild(title);
      const actions=document.createElement('div');actions.className='actions';
      const show=document.createElement('button');show.textContent='Arată pe hartă';show.onclick=()=>{
        const coords=zone.geometry.coordinates[0];const bounds=new maplibregl.LngLatBounds();coords.forEach(p=>bounds.extend(p));map.fitBounds(bounds,{padding:70,maxZoom:17});};
      const remove=document.createElement('button');remove.textContent='Șterge';remove.onclick=()=>{
        if(!confirm(`Ștergi zona „${zone.properties.name}”?`))return;
        zones.splice(i,1);persist();list();update();};
      actions.append(show,remove);box.appendChild(actions);container.appendChild(box);});
  }
  map.on('load',()=>{
    map.addSource('saved',{type:'geojson',data:empty()});map.addSource('draft',{type:'geojson',data:empty()});
    map.addLayer({id:'saved-fill',type:'fill',source:'saved',paint:{'fill-color':'#16b8a0','fill-opacity':0.20}});
    map.addLayer({id:'saved-line',type:'line',source:'saved',paint:{'line-color':'#34dfc0','line-width':2}});
    map.addLayer({id:'draft-line',type:'line',source:'draft',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#ffd05a','line-width':3}});
    map.addLayer({id:'draft-points',type:'circle',source:'draft',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':5,'circle-color':'#ffd05a','circle-stroke-color':'#282d32','circle-stroke-width':2}});
    update();
  });
  map.on('click',event=>{if(!drawing)return;if(points.length>=500){message('Maximum 500 de puncte.');return;}
    points.push([event.lngLat.wrap().lng,event.lngLat.lat]);message(`${points.length} puncte. Adaugă puncte sau închide forma.`);update();});
  el('start').onclick=()=>{drawing=true;closed=false;points=[];message('Apasă pe hartă pentru a adăuga puncte.');controls();update();};
  el('undo').onclick=()=>{points.pop();update();};
  el('cancel').onclick=()=>{drawing=false;closed=false;points=[];message('Desenul a fost anulat.');update();};
  el('finish').onclick=()=>{const ring=[...points,points[0]];if(!ringValid(ring)){message('Formă invalidă: verifică punctele și liniile care se intersectează.');return;}
    drawing=false;closed=true;message('Forma este închisă. Introdu un nume și salvează zona.');update();};
  el('save').onclick=()=>{const name=el('zoneName').value.trim();if(!name){message('Introdu numele zonei.');return;}
    if(zones.length>=200){message('Maximum 200 de zone într-un proiect.');return;}
    zones.push({type:'Feature',properties:{name},geometry:{type:'Polygon',coordinates:[[...points,points[0]]]}});
    const ok=persist();drawing=false;closed=false;points=[];el('zoneName').value='';if(ok)message('Zonă salvată în browser. Descarcă proiectul pentru backup.');list();update();};
  el('download').onclick=()=>{const data=JSON.stringify({type:'FeatureCollection',features:zones},null,2);
    const url=URL.createObjectURL(new Blob([data],{type:'application/geo+json'}));const a=document.createElement('a');
    a.href=url;a.download='terragen-zone.geojson';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  el('open').onclick=()=>el('file').click();
  el('file').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{
    if(file.size>2000000)throw new Error('Fișier prea mare.');const data=JSON.parse(await file.text());
    if(data.type!=='FeatureCollection'||!Array.isArray(data.features)||data.features.length>200||!data.features.every(goodFeature))throw new Error('Format GeoJSON invalid.');
    if(zones.length&&!confirm('Înlocuiești zonele curente cu cele din fișier?'))return;
    zones=data.features.map(f=>({type:'Feature',properties:{name:f.properties.name.trim()},geometry:f.geometry}));
    drawing=false;closed=false;points=[];const ok=persist();list();update();if(ok)message('Proiectul a fost încărcat.');
  }catch(err){message('Nu pot încărca proiectul: '+err.message);}finally{e.target.value='';}};
  try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(saved)&&saved.length<=200&&saved.every(goodFeature))zones=saved;}
  catch(e){message('Nu am putut citi zonele salvate.');}list();controls();
})();
