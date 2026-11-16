/* Display of BACKEND-classified faces. The browser never decides identity or class membership.
   IN_CLASS  -> green, name + code + "Đã điểm danh" (only when SQLite confirms) / "Chờ xác nhận"
   UNKNOWN   -> red, exactly "Không xác định" (no name, no code)
   PROCESSING / no result / stale -> grey box, no text */
"use strict";
((root) => {
  const COLORS={in_class:'#16a34a',verified:'#16a34a',unknown:'#dc2626',processing:'#9ca3af'};
  function describe(face, stale=false, present=false) {
    if (!face || stale || face.status==='PROCESSING' || !face.status)
      return {state:'processing',color:COLORS.processing,lines:[],name:'',code:'',verified:false};
    const person=face.student;
    if (face.status==='IN_CLASS' && person) {
      const saved=present===true||face.attendance?.saved===true;
      return {state:'in_class',color:COLORS.in_class,lines:[person.full_name,person.student_code,saved?'Đã điểm danh':'Chờ xác nhận'],
        name:person.full_name,code:person.student_code,verified:true,saved};
    }
    if (face.status==='VERIFIED' && person)
      return {state:'verified',color:COLORS.verified,lines:[person.full_name,person.student_code],name:person.full_name,code:person.student_code,verified:true};
    return {state:'unknown',color:COLORS.unknown,lines:['Không xác định'],name:'Không xác định',code:'',verified:false};
  }
  function canConfirmManually(face,record,active,sessionId){
    const person=face?.student;
    return !!(face?.status==='IN_CLASS'&&active?.status==='IN_PROGRESS'&&active.id===sessionId&&person&&record&&
      record.student_id===person.id&&record.attendance_status!=='PRESENT');
  }
  root.RecognitionDisplay={describe,canConfirmManually,COLORS};
})(globalThis);
