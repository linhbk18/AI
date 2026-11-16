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
