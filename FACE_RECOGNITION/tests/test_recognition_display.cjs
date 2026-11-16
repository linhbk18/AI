// Display-contract tests only; not biometric evaluation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/static/js/recognition_display.js'),'utf8'),context);
const describe=context.RecognitionDisplay.describe;
const face={top1:{student:{full_name:'Student A',student_code:'SV001'},similarity:.71},identity_confirmed:false};
let d=describe(face,false,true);
assert.match(d.name,/Gợi ý:/);assert.equal(d.verified,false);assert.ok(!d.lines.includes('Đã điểm danh'));
assert.equal(describe(face,true,true).lines.length,0);
assert.equal(describe({}).name,'Không xác định');
d=describe({...face,identity_confirmed:true},false,true);assert.ok(d.lines.includes('Đã điểm danh'));
d=describe({...face,outside_class:true},false,false);assert.match(d.name,/Gợi ý:/);
d=describe({...face,identity_confirmed:true,outside_class:true});assert.equal(d.name,'Không thuộc lớp');
console.log('6 display scenarios passed.');

// Regression: unverified AI suggestions require human review, never automatic verification.
const can=context.RecognitionDisplay.canConfirmManually;
const active={id:12,status:'IN_PROGRESS'},record={student_id:1,attendance_status:null};
const candidate={top1:{student:{id:1}},identity_confirmed:false,eligible_for_confirmation:true,outside_class:false};
assert.equal(can(candidate,record,active,12),true);
assert.equal(candidate.identity_confirmed,false);
assert.equal(can({...candidate,outside_class:true},record,active,12),false);
assert.equal(can(candidate,record,active,99),false);
assert.equal(can(candidate,{...record,attendance_status:'PRESENT'},active,12),false);
assert.equal(can(candidate,record,{...active,status:'COMPLETED'},12),false);
console.log('5 manual-confirmation regression scenarios passed.');
