// Presentation only: the complete business answer stays in the message store.
export function createTypewriter({onUpdate,onDone=()=>{},schedule=setTimeout,cancel=clearTimeout}) {
 let timer=null,units=[],position=0,active=false
 const clear=()=>{if(timer!=null)cancel(timer);timer=null}
 const publish=()=>onUpdate(units.slice(0,position).join(''),active)
 function tick(){timer=null;position=Math.min(units.length,position+Math.max(2,Math.ceil(units.length/800)));active=position<units.length;publish();if(active)timer=schedule(tick,24);else onDone()}
 return {
  start(text,animate=true){clear();const value=String(text || '');units=typeof Intl.Segmenter==='function'?Array.from(new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(value),x=>x.segment):Array.from(value);position=animate?0:units.length;active=animate&&units.length>0;publish();if(active)timer=schedule(tick,24)},
  update(text,animate=true){
   const value=String(text || ''),previous=units.join('')
   if(!value.startsWith(previous)){this.start(value,animate);return}
   units=typeof Intl.Segmenter==='function'?Array.from(new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(value),x=>x.segment):Array.from(value)
   if(!animate){this.finish();return}
   active=position<units.length;publish()
   if(active && timer==null)timer=schedule(tick,24)
  },
  finish(){clear();position=units.length;const wasActive=active;active=false;publish();if(wasActive)onDone()},
  dispose(){clear();active=false}
 }
}
