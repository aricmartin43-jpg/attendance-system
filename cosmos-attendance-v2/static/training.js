'use strict';
let trainingData, trainingScene=0;
async function refreshTraining(lessonId='welding-ta-v1'){
  trainingData=await api('/api/training?lesson_id='+encodeURIComponent(lessonId));trainingScene=0;
  const d=trainingData;
  $('training-content').innerHTML=`<article class="section-card"><span class="eyebrow">COSMOS · TAMIL SAFETY LIBRARY</span><label for="training-module">உங்கள் வேலைக்கான பயிற்சியைத் தேர்வு செய்யுங்கள்</label><select id="training-module">${d.lessons.map(l=>`<option value="${escapeHTML(l.id)}" ${l.id===d.lesson.id?'selected':''}>${escapeHTML(l.title)} · ${escapeHTML(l.role)}</option>`).join('')}</select><h2>${escapeHTML(d.lesson.title)}</h2><p class="help">தமிழ் குரல் மற்றும் பெரிய தமிழ் வரிகளுடன் கார்ட்டூன் விளக்க வீடியோ. உள்ளடக்கத்தை உங்கள் பணிமனை நடைமுறையுடன் மேற்பார்வையாளர் சரிபார்க்க வேண்டும்.</p><video class="training-video" controls playsinline preload="none" poster="${escapeHTML(d.lesson.poster_url)}" aria-label="${escapeHTML(d.lesson.title)}"><source src="${escapeHTML(d.lesson.video_url)}" type="video/mp4"><p>வீடியோவை இயக்க முடியவில்லை. கீழே உள்ள பாடத்தைப் படிக்கவும்.</p></video><p class="help">கைபேசியை பக்கவாட்டில் திருப்பி முழுத் திரையில் பார்க்கலாம். தேவையான இடத்தில் நிறுத்தி மீண்டும் பாருங்கள்.</p><p><a href="${escapeHTML(d.lesson.video_url)}" download>வீடியோவை பதிவிறக்கவும்</a></p><h3>பாடத்தை மீண்டும் படிக்கவும்</h3><div id="training-scene"></div><div class="training-controls"><button class="secondary" id="training-prev">← முந்தையது</button><button class="secondary" id="training-next">அடுத்தது →</button></div><label><input type="checkbox" id="training-motion" checked> காட்சி அசைவு</label><p class="help">இந்தப் பயிற்சியும் தேர்வும் மட்டும் இயந்திரம் இயக்கும் அனுமதி அல்ல.</p><details><summary>பயிற்சி ஆதாரங்கள்</summary>${d.lesson.sources.map(s=>`<p><a href="${escapeHTML(s)}" target="_blank" rel="noopener noreferrer">${escapeHTML(s)}</a></p>`).join('')}</details></article>
  <article class="section-card"><h2>மதிப்பீடு · 10 கேள்விகள்</h2><p>தேர்ச்சி: 8 / 10. தவறான பதில்களுக்கான விளக்கம் சமர்ப்பித்த பின் வரும்.</p><form id="training-quiz">${d.questions.map(q=>`<fieldset class="training-question"><legend>${q.id+1}. ${escapeHTML(q.text)}</legend>${q.options.map((o,i)=>`<label><input type="radio" name="q${q.id}" value="${i}" required> ${escapeHTML(o)}</label>`).join('')}</fieldset>`).join('')}<label><input type="checkbox" name="reviewed" required> மேலுள்ள பாடக் காட்சிகளைப் படித்தேன்.</label><button class="primary" type="submit">பதில்களைச் சமர்ப்பிக்கவும்</button></form><div id="training-feedback" aria-live="polite"></div></article><article class="section-card"><h2>${user.admin?'குழு பயிற்சி பதிவுகள்':'எனது பயிற்சி பதிவுகள்'}</h2><p class="help">சமீபத்திய 200 பதிவுகள் வரை. நடைமுறைச் சோதனைக்கு நிர்வாக அணுகல் உள்ள மேற்பார்வையாளர் தேவை.</p><div id="training-records"></div></article>`;
  renderTrainingScene();renderTrainingRecords(d.attempts);
  $('training-module').onchange=e=>perform(()=>refreshTraining(e.target.value));
  $('training-prev').onclick=()=>{trainingScene=Math.max(0,trainingScene-1);renderTrainingScene();};
  $('training-next').onclick=()=>{trainingScene=Math.min(d.lesson.scenes.length-1,trainingScene+1);renderTrainingScene();};
  $('training-motion').onchange=e=>$('training-scene').classList.toggle('training-still',!e.target.checked);
  $('training-quiz').onsubmit=e=>{e.preventDefault();perform(async()=>{
    const button=e.target.querySelector('button');button.disabled=true;
    try{const f=new FormData(e.target),r=await api('/api/training/attempts','POST',{lesson_id:d.lesson.id,reviewed_lesson:f.has('reviewed'),answers:d.questions.map(q=>Number(f.get('q'+q.id)))});
      $('training-feedback').innerHTML=`<h3>${r.score}/10 · ${r.passed?'தேர்ச்சி — நடைமுறைச் சோதனை நிலுவையில்':'மீண்டும் பாடத்தைப் படித்து முயற்சிக்கவும்'}</h3>${r.feedback.map(x=>`<p><strong>${x.correct?'✓':'✗'} ${escapeHTML(x.question)}</strong><br>${escapeHTML(x.correct_answer)} — ${escapeHTML(x.explanation)}</p>`).join('')}`;
      e.target.reset();trainingData=await api('/api/training?lesson_id='+encodeURIComponent(d.lesson.id));renderTrainingRecords(trainingData.attempts);
    }finally{button.disabled=false;}
  });};
}
function renderTrainingScene(){
  const [title,text]=trainingData.lesson.scenes[trainingScene];
  const sceneImages=trainingData.lesson.id==='welding-ta-v1'?['welding-1','welding-2','welding-3','welding-4','welding-5','welding-1','welding-6']:null;
  const imageUrl=sceneImages?'/static/training-media/'+sceneImages[trainingScene]+'.jpg':trainingData.lesson.poster_url;
  $('training-scene').innerHTML=`<div class="training-illustration"><img src="${escapeHTML(imageUrl)}" alt="${escapeHTML(title)} — பணிமனை பாதுகாப்பு கார்ட்டூன்"></div><p class="eyebrow">${trainingScene+1} / ${trainingData.lesson.scenes.length}</p><h3>${escapeHTML(title)}</h3><p>${escapeHTML(text)}</p>`;
  $('training-prev').disabled=trainingScene===0;$('training-next').disabled=trainingScene===trainingData.lesson.scenes.length-1;
}
function renderTrainingRecords(rows){
  $('training-records').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>Employee</th><th>Lesson</th><th>Date</th><th>Quiz</th><th>Practical</th><th>Reviewer / notes</th>${user.admin?'<th>Review</th>':''}</tr></thead><tbody>${rows.map(r=>`<tr><td>${escapeHTML(r.employee)}<small>${escapeHTML(r.code)}</small></td><td>${escapeHTML(r.lesson_title)}</td><td>${escapeHTML(new Date(r.date).toLocaleString('en-IN'))}</td><td>${r.score}/10 · ${r.passed?'Pass':'Retry'}</td><td>${escapeHTML(r.review)}</td><td>${escapeHTML(r.reviewer||'—')}<small>${escapeHTML(r.notes)}</small></td>${user.admin?`<td>${r.passed&&r.review==='Pending'?`<button class="small-button" data-training-review="${r.id}">Practical check</button>`:'—'}</td>`:''}</tr>`).join('')}</tbody></table></div>`:'<p>பதிவுகள் இன்னும் இல்லை.</p>';
  $('training-records').onclick=e=>{const b=e.target.closest('[data-training-review]');if(!b)return;
    const r=rows.find(x=>x.id===Number(b.dataset.trainingReview));
    const box=document.createElement('form');box.className='section-card';
    box.innerHTML=`<h3>Practical review · ${escapeHTML(r.employee)}</h3><p>Observe the employee performing the selected work safely against the approved site procedure, including required PPE, safeguards, pre-start checks, shutdown and hazard reporting. This record does not grant equipment authorisation.</p><label>Result<select name="result"><option>Passed</option><option>Needs retraining</option></select></label><label>Observations<textarea name="notes" minlength="10" maxlength="2000" required></textarea></label><label><input type="checkbox" name="observed" required> I personally observed this practical check.</label><button class="primary">Save review</button><button type="button" class="secondary" data-cancel>Cancel</button>`;
    $('training-records').append(box);b.disabled=true;box.querySelector('[data-cancel]').onclick=()=>{box.remove();b.disabled=false;};
    box.onsubmit=event=>{event.preventDefault();perform(async()=>{const f=new FormData(box);await api('/api/training/attempts/'+r.id+'/review','PATCH',{result:f.get('result'),notes:f.get('notes'),observed_practical:f.has('observed')});trainingData=await api('/api/training');renderTrainingRecords(trainingData.attempts);});};
  };
}
