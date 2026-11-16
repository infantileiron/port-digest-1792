/** @typedef {{id:number,code:string,station:string,planned_at:string,actual_at:string|null,note:string}} Arrival */
/** @typedef {{id:number,name:string,station:string,daily_at:string,recipient:string,enabled:boolean,next_run_at:string}} Subscription */
/** @typedef {{id:number,subscription_id:number,report_day:string,state:string,payload:object,review_note:string}} Receipt */
/** @typedef {'pending'|'scanning'|'held'|'completed'} ReconBatchState */
/** @typedef {null|'missing'|'corrupt'|'crossed'|'contradiction'|'orphan'} ReconFinding */
/** @typedef {{id:number,state:ReconBatchState,created_at:string,started_at:string|null,finished_at:string|null,totals:object,note:string}} ReconBatch */
/** @typedef {{id:number,batch_id:number,receipt_id:number|null,artifact_name:string|null,finding:ReconFinding,disposition:string,evidence:object,created_at:string,updated_at:string}} ReconItem */
