const fs=require('fs');
const path=require('path');
const {spawn}=require('child_process');
const manifest=path.join(__dirname,'index.json');
let timer=null;
function run(){const child=spawn(process.execPath,[path.join(__dirname,'generate.js')],{stdio:'inherit'});child.on('exit',code=>{if(code)console.error('generate.js exited with code '+code)})}
fs.watch(manifest,()=>{clearTimeout(timer);timer=setTimeout(()=>{console.log('🔄 Detected change → regenerating...');run()},150)});
console.log('Watching competitions/index.json...');
run();
