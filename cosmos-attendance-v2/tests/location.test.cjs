const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/location.js'),'utf8');
const fresh=(extra={})=>({coords:{latitude:11.01,longitude:76.96,accuracy:15,...extra},timestamp:Date.now()});
function environment(actions,extra={}) {
  const calls=[];
  const context={navigator:{geolocation:{getCurrentPosition(ok,fail,options){calls.push(options);actions.shift()(ok,fail);}}},setTimeout,clearTimeout,Date,...extra};
  vm.runInNewContext(source,context);
  return {get:context.CosmosLocation.getLocation,calls};
}
test('successful high-accuracy location needs one request',async()=>{
  const e=environment([(ok)=>ok(fresh())]);const result=await e.get();
  assert.equal(result.lat,11.01);assert.equal(result.accuracy,15);assert.equal(e.calls.length,1);assert.equal(e.calls[0].enableHighAccuracy,true);
});
test('timeout retries with standard accuracy without changing the reading',async()=>{
  const sample=fresh({accuracy:600}),progress=[];
  const e=environment([(ok,fail)=>fail({code:3}),ok=>ok(sample)]);
  const result=await e.get(message=>progress.push(message));
  assert.equal(e.calls.length,2);assert.equal(e.calls[1].enableHighAccuracy,false);assert.equal(e.calls[1].maximumAge,0);
  assert.equal(result.timestamp,sample.timestamp);assert.equal(result.accuracy,600);assert.match(progress[1],/Retrying/);
});
test('permission denial is explained and never retried automatically',async()=>{
  const e=environment([(ok,fail)=>fail({code:1})]);
  await assert.rejects(e.get(),error=>error.code==='PERMISSION_DENIED'&&/phone settings/.test(error.message));assert.equal(e.calls.length,1);
});
test('unavailable locations stop after one retry',async()=>{
  const e=environment([(ok,fail)=>fail({code:2}),(ok,fail)=>fail({code:2})]);
  await assert.rejects(e.get(),error=>error.code==='POSITION_UNAVAILABLE');assert.equal(e.calls.length,2);
});
test('stale and inaccurate readings are not accepted',async()=>{
  const e=environment([ok=>ok({...fresh(),timestamp:Date.now()-121000}),ok=>ok(fresh({accuracy:10001}))]);
  await assert.rejects(e.get(),error=>error.code==='LOW_ACCURACY');assert.equal(e.calls.length,2);
});
test('missing and non-finite coordinates are not accepted',async()=>{
  const e=environment([ok=>ok({timestamp:Date.now()}),ok=>ok(fresh({latitude:NaN}))]);
  await assert.rejects(e.get(),error=>error.code==='INVALID_LOCATION');
});
test('unsupported or insecure contexts show guidance without a request',async()=>{
  const insecure=environment([],{isSecureContext:false});
  await assert.rejects(insecure.get(),error=>error.code==='INSECURE_CONTEXT');assert.equal(insecure.calls.length,0);
  const unsupported=environment([],{navigator:{}});
  await assert.rejects(unsupported.get(),error=>error.code==='UNSUPPORTED');
});
test('an unanswered permission prompt ends without starting another request',async()=>{
  let expire;
  const e=environment([()=>{}],{setTimeout:callback=>{expire=callback;return 1},clearTimeout:()=>{}});
  const pending=e.get();expire();
  await assert.rejects(pending,error=>error.code==='PERMISSION_WAIT');assert.equal(e.calls.length,1);
});
