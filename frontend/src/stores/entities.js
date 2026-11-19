import {nodesAPI,scenariosAPI,drillsAPI,pagesAPI} from '../api/entities.js';
export const store={nodes:[],scenarios:[],drills:[],pages:[],repairs:[],pageHealth:[],async refresh(){[this.nodes,this.scenarios,this.drills,this.pages,this.pageHealth,this.repairs]=await Promise.all([nodesAPI.list(),scenariosAPI.list(),drillsAPI.list(),pagesAPI.copies(),pagesAPI.health(),pagesAPI.jobs()]);}};
