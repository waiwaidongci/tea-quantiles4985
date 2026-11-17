export const ROLES=['admin','analyst','viewer'];
export const STATES=['queued','running','completed','failed'];
export const STATE_LABELS={queued:'等待计算',running:'计算进行中',completed:'已完成',failed:'失败需修复',destroyed:'已销毁'};
export const DESTRUCTION_STATES=['pending','cleaning','completed','failed'];
export const DESTRUCTION_LABELS={pending:'等待清理',cleaning:'清理进行中',completed:'已清理',failed:'清理失败待恢复'};
