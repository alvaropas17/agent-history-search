
"use strict";
const TOKEN=new URLSearchParams(location.search).get("t")||"";
const $=id=>document.getElementById(id);
const q=$("q"),host=$("host"),list=$("list"),detail=$("detail"),text=$("text");
let results=[],words=[],sel=-1,seq=0,timer=0,cur=null,curText="";
function el(tag,cls,txt){const e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
function toast(m){const t=$("toast");t.textContent=m;t.classList.add("on");setTimeout(()=>t.classList.remove("on"),1200)}
async function api(path,params){
  const u=new URL(path,location.href);u.search=new URLSearchParams(params).toString();
  const r=await fetch(u,{headers:{"X-Token":TOKEN},cache:"no-store"});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function hl(parent,s,ws){
  const ws2=ws.filter(w=>w.length>=3);
  if(!ws2.length){parent.append(document.createTextNode(s));return}
  const low=s.toLowerCase();let i=0;
  while(i<s.length){
    let best=-1,bl=0;
    for(const w of ws2){const p=low.indexOf(w.toLowerCase(),i);if(p>=0&&(best<0||p<best)){best=p;bl=w.length}}
    if(best<0){parent.append(document.createTextNode(s.slice(i)));break}
    if(best>i)parent.append(document.createTextNode(s.slice(i,best)));
    parent.append(el("mark",null,s.slice(best,best+bl)));i=best+bl}
}
function fmt(ts){const d=new Date(ts);const p=n=>String(n).padStart(2,"0");return d.getFullYear()+"-"+p(d.getMonth()+1)+"-"+p(d.getDate())+" "+p(d.getHours())+":"+p(d.getMinutes())}
function render(){
  list.replaceChildren();
  if(!results.length){const li=el("li",null,q.value.trim()?"Sin resultados":"Escribe para buscar");li.id="empty";list.append(li);return}
  results.forEach((r,i)=>{
    const li=el("li",i===sel?"sel":"");
    const m=el("div","meta");m.append(el("span",null,fmt(r.timestamp)),el("span","agent",r.host),el("span",null,r.role));
    li.append(m,el("div","title",r.title||r.session));
    const sn=el("div","snip");hl(sn,r.text.replace(/\s+/g," "),words);li.append(sn);
    li.addEventListener("click",()=>{sel=i;render();openDetail()});
    list.append(li)});
  const s=list.querySelector(".sel");if(s)s.scrollIntoView({block:"nearest"});
}
async function search(){
  const my=++seq,v=q.value.trim();
  if(!v){results=[];sel=-1;closeDetail();render();return}
  try{const j=await api("api/search",{q:v,host:host.value});
    if(my!==seq)return;$("index-status").textContent=j.index_update?.message||"Índice local; rama activa no verificada.";results=j.results;words=j.words||[];sel=results.length?0:-1;closeDetail();render()}
  catch(e){if(my!==seq)return;results=[];list.replaceChildren(el("li",null,"Error: "+e.message))}
}
function setDetail(j,append){
  curText=append?curText+j.text:j.text;
  text.replaceChildren();hl(text,curText,words);
  cur.next=j.next_offset;$("b-more").hidden=j.next_offset==null;
}
async function openDetail(){
  const r=results[sel];if(!r)return;cur={r,next:null};
  detail.hidden=false;
  $("dmeta").textContent=fmt(r.timestamp)+" | "+r.host+" | "+(r.title||r.session)+" | "+r.session;
  $("b-resume").hidden=r.host!=="claude";
  text.textContent="Cargando...";
  const current=cur;
  try{const j=await api("api/expand",{ref:r.ref,context:1500});if(cur===current)setDetail(j,false)}catch(e){if(cur===current)text.textContent="Error: "+e.message}
}
async function more(){
  if(!cur||cur.next==null)return;
  const current=cur;
  try{const j=await api("api/expand",{ref:current.r.ref,context:3000,offset:current.next});if(cur===current)setDetail(j,true)}catch(e){if(cur===current)toast("Error: "+e.message)}
}
function closeDetail(){detail.hidden=true;cur=null;q.focus()}
async function copy(s,label){
  try{await navigator.clipboard.writeText(s)}catch(_){const t=el("textarea");t.value=s;document.body.append(t);t.select();document.execCommand("copy");t.remove()}
  toast("Copiado: "+label)}
q.addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(search,120)});
host.addEventListener("change",search);
$("b-frag").onclick=()=>copy(curText,"fragmento");
$("b-id").onclick=()=>cur&&copy(cur.r.session,"ID de sesion");
$("b-resume").onclick=()=>cur&&copy("claude --resume "+cur.r.session,"comando");
$("b-more").onclick=more;$("b-close").onclick=closeDetail;
document.addEventListener("keydown",e=>{
  if(e.key==="ArrowDown"||e.key==="ArrowUp"){if(!results.length)return;e.preventDefault();
    sel=Math.max(0,Math.min(results.length-1,sel+(e.key==="ArrowDown"?1:-1)));render();if(!detail.hidden)openDetail()}
  else if(e.key==="Enter"){if(sel>=0){e.preventDefault();openDetail()}}
  else if(e.key==="Escape"){if(!detail.hidden)closeDetail();else if(q.value){q.value="";search()}else window.close()}
});
q.focus();render();
