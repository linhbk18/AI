// Display-contract tests only (node tests/test_recognition_display.cjs); not biometric evaluation.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/static/js/recognition_display.js'),'utf8'),context);
const {describe,canConfirmManually:can,COLORS}=context.RecognitionDisplay;
const student={id:1,full_name:'Student A',student_code:'DEMO-SV001'};

// A. IN_CLASS -> green, name + code; "Đã điểm danh" ONLY when SQLite confirmed.
let d=describe({status:'IN_CLASS',student,attendance:{saved:false}});
assert.equal(d.color,COLORS.in_class);assert.deepEqual([...d.lines],['Student A','DEMO-SV001','Chờ xác nhận']);
d=describe({status:'IN_CLASS',student,attendance:{saved:true}});assert.equal(d.lines[2],'Đã điểm danh');
d=describe({status:'IN_CLASS',student},false,true);assert.equal(d.lines[2],'Đã điểm danh');
// B. UNKNOWN -> red, exactly "Không xác định"; even if a student object were present it is not shown.
d=describe({status:'UNKNOWN',student:null,label:'Không xác định'});
assert.equal(d.color,COLORS.unknown);assert.deepEqual([...d.lines],['Không xác định']);
d=describe({status:'UNKNOWN',student});assert.deepEqual([...d.lines],['Không xác định']);assert.ok(!JSON.stringify(d).includes('Student A'));
// C. PROCESSING / no result / stale -> grey, no text.
for(const f of [null,undefined,{status:'PROCESSING'},{}])assert.equal(describe(f).lines.length,0);
assert.equal(describe({status:'IN_CLASS',student},true).lines.length,0);
assert.equal(describe({status:'PROCESSING'}).color,COLORS.processing);
// No legacy outsider labels anywhere in the display module.
const source=fs.readFileSync(path.join(__dirname,'../frontend/static/js/recognition_display.js'),'utf8');
for(const banned of ['Ngoài lớp','Không thuộc lớp','Gợi ý'])assert.ok(!source.includes(banned),banned);
console.log('display scenarios passed (green/red/grey).');

// Manual confirmation button only for IN_CLASS faces of the active open session.
const active={id:12,status:'IN_PROGRESS'},record={student_id:1,attendance_status:null};
const inClass={status:'IN_CLASS',student};
assert.equal(can(inClass,record,active,12),true);
assert.equal(can({status:'UNKNOWN',student:null},record,active,12),false);
assert.equal(can(inClass,record,active,99),false);
assert.equal(can(inClass,{...record,attendance_status:'PRESENT'},active,12),false);
assert.equal(can(inClass,record,{...active,status:'COMPLETED'},12),false);
console.log('manual-confirmation scenarios passed.');
