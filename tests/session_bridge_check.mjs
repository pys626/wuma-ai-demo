// Browser-storage protocol tests, without a real browser or server.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../ui_session_component/bridge.js',import.meta.url),'utf8');
const {default:bridge}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const values=new Map();
globalThis.window={sessionStorage:{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)}};
const calls=[];
let request=0;
function run(operation,options={}){
 bridge({data:{operation,request:String(++request),storage_key:'fixture',...options},setStateValue:(_key,value)=>calls.push(value)});
 return calls.at(-1);
}
assert.equal(run('read').token,'');
const beforeWrite=calls.length;
run('write',{token:'A'.repeat(43),view:{page:'AI答辩',submission_id:3}});
assert.equal(calls.length,beforeWrite,'Successful persistence must not race a new user action with a callback rerun');
assert.equal(JSON.parse(values.get('fixture')).view.submission_id,3);
assert.equal(run('read').token,'A'.repeat(43));
assert.equal(run('read').view.page,'AI答辩');
const stable={operation:'write',request:'stable',storage_key:'fixture',token:'A'.repeat(43),view:{page:'任务中心'}};
bridge({data:stable,setStateValue:(_key,value)=>calls.push(value)});
const count=calls.length;
bridge({data:stable,setStateValue:(_key,value)=>calls.push(value)});
assert.equal(calls.length,count,'Repeated render must not start an endless component rerun');
bridge({data:{...stable,view:{page:'学习档案'}},setStateValue:(_key,value)=>calls.push(value)});
assert.equal(JSON.parse(values.get('fixture')).view.page,'学习档案');
assert.equal(calls.length,count,'Navigation saves without a redundant acknowledgment rerun');
run('clear');assert.equal(values.size,0);
values.set('fixture','broken{');assert.equal(run('read').status,'corrupt');assert.equal(values.size,0);
values.set('fixture','null');assert.equal(run('read').status,'corrupt');assert.equal(values.size,0);
window.sessionStorage.getItem=()=>{throw new Error('storage unavailable')};
assert.equal(run('read').status,'blocked');
window.sessionStorage.getItem=key=>values.get(key)||null;
run('write',{token:'B'.repeat(43),view:{page:'首页'}});
assert.equal(calls.at(-1).status,'ready','Recovery from blocked storage must still notify Python');
console.log('浏览器存储协议10项检查全部通过。');
