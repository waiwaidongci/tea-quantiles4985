import {studies,runs} from '../api/research.js';
export const store={studies:[],runs:[],studyId:0,runId:0,rows:[]};
export async function load(){[store.studies,store.runs]=await Promise.all([studies.list(),runs.list()]);const current=store.studies.find(s=>s.id===store.studyId);if(!current||current.sealed)store.studyId=store.studies.find(s=>!s.sealed)?.id||0;}
export async function loadRows(){store.rows=store.studyId?await studies.rows(store.studyId):[];}
