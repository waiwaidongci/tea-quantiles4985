import {STATE_LABELS,DESTRUCTION_LABELS} from '../../types/enums.js';
export const status=x=>`<span class="status ${x}">${STATE_LABELS[x]||x}</span>`;
export const destructionStatus=x=>`<span class="status ${x}">${DESTRUCTION_LABELS[x]||x}</span>`;
