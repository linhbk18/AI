/* Display-only suggestions never authorize an attendance write. */
"use strict";
((root) => {
  function describe(face, stale=false, present=false) {
    if (!face || stale) return {lines:[],name:'',code:'',verified:false};
    const person=face.top1?.student;
    if (!person || !Number.isFinite(face.top1?.similarity))
      return {lines:['Không xác định'],name:'Không xác định',code:'',verified:false};
    if (face.identity_confirmed === true) {
      if (face.outside_class) return {lines:['Không thuộc lớp'],name:'Không thuộc lớp',code:'',verified:true};
      return {lines:[person.full_name,person.student_code,present?'Đã điểm danh':'Đã xác minh'],name:person.full_name,code:person.student_code,verified:true};
    }
    return {lines:['Gợi ý: '+person.full_name,person.student_code,'Chưa xác minh · không tự điểm danh'],name:'Gợi ý: '+person.full_name,code:person.student_code,verified:false};
  }
  root.RecognitionDisplay={describe};
})(globalThis);
