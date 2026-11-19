import {api} from './client.js';
export const nodesAPI={list:()=>api('/nodes'),save:(data,id)=>api('/nodes'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(data)})};
export const scenariosAPI={list:()=>api('/scenarios'),create:data=>api('/scenarios',{method:'POST',body:JSON.stringify(data)})};
export const drillsAPI={list:()=>api('/drills'),get:id=>api('/drills/'+id),create:data=>api('/drills',{method:'POST',body:JSON.stringify(data)})};
export const pagesAPI={
 copies:()=>api('/pages'),
 health:()=>api('/pages/health'),
 jobs:()=>api('/pages/repair'),
 corrupt:copy_id=>api('/pages/corrupt',{method:'POST',body:JSON.stringify({copy_id})}),
 rewrite:page_key=>api('/pages/rewrite',{method:'POST',body:JSON.stringify({page_key})}),
 startRepair:(copy_id,repair_key)=>api('/pages/repair',{method:'POST',body:JSON.stringify(repair_key?{copy_id,repair_key}:{copy_id})}),
 advance:(id,simulate_interrupt)=>api('/pages/repair/'+id+'/advance',{method:'POST',body:JSON.stringify({simulate_interrupt:!!simulate_interrupt})}),
 probe:(page_key,node_id)=>api('/pages/probe',{method:'POST',body:JSON.stringify({page_key,node_id})})
};
