const REPO_URL = "https://github.com/OWNER/comfy-colab-kit";

// Animated aurora background — vanilla WebGL port inspired by React Bits "Aurora"
// (https://reactbits.dev, MIT License, (c) David Haz). Falls back to CSS gradient without WebGL
// and renders a single static frame under prefers-reduced-motion.
(() => {
  const c = document.getElementById("bg"); if (!c) return;
  const gl = c.getContext("webgl", { premultipliedAlpha: true, alpha: true, antialias: false });
  if (!gl) { c.remove(); return; }
  const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
  const fs = `precision highp float;uniform float t;uniform vec2 r;uniform vec3 c0,c1,c2;uniform float amp;
  vec3 perm(vec3 x){return mod(((x*34.)+1.)*x,289.);}
  float sn(vec2 v){const vec4 C=vec4(.211324865,.366025404,-.577350269,.024390244);vec2 i=floor(v+dot(v,C.yy));
   vec2 x0=v-i+dot(i,C.xx);vec2 i1=x0.x>x0.y?vec2(1.,0.):vec2(0.,1.);vec4 x12=x0.xyxy+C.xxzz;x12.xy-=i1;i=mod(i,289.);
   vec3 p=perm(perm(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));vec3 m=max(.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);
   m=m*m;m=m*m;vec3 x=2.*fract(p*C.www)-1.;vec3 h=abs(x)-.5;vec3 ox=floor(x+.5);vec3 a0=x-ox;
   m*=1.79284291-.85373472*(a0*a0+h*h);vec3 g;g.x=a0.x*x0.x+h.x*x0.y;g.yz=a0.yz*x12.xz+h.yz*x12.yw;return 130.*dot(m,g);}
  void main(){vec2 uv=gl_FragCoord.xy/r;vec3 ramp=uv.x<.5?mix(c0,c1,uv.x*2.):mix(c1,c2,(uv.x-.5)*2.);
   float h=sn(vec2(uv.x*2.+t*.1,t*.25))*.5*amp;h=exp(h);h=(uv.y*2.-h+.2);float i=.6*h;
   float a=smoothstep(.2-.5,.2+.5,i);vec3 col=i*ramp;gl_FragColor=vec4(col*a,a)*.55;}`;
  const sh = (ty, s) => { const o = gl.createShader(ty); gl.shaderSource(o, s); gl.compileShader(o); return o; };
  const pr = gl.createProgram(); gl.attachShader(pr, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(pr); if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) { c.remove(); return; } gl.useProgram(pr);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
  const lp = gl.getAttribLocation(pr, "p"); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp, 2, gl.FLOAT, false, 0, 0);
  const U = n => gl.getUniformLocation(pr, n), hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  const pal = () => { const cs = getComputedStyle(document.documentElement);
    ["c0", "c1", "c2"].forEach((k, i) => gl.uniform3fv(U(k), hex(cs.getPropertyValue("--a" + i).trim() || "#F9AB00"))); };
  let amp = 1; window.kitBg = { set(o) { if (o.amp != null) amp = o.amp; pal(); } };
  const rm = matchMedia("(prefers-reduced-motion: reduce)");
  const size = () => { const d = Math.min(devicePixelRatio || 1, 1.5) * .5; c.width = innerWidth * d; c.height = innerHeight * d;
    gl.viewport(0, 0, c.width, c.height); gl.uniform2f(U("r"), c.width, c.height); };
  addEventListener("resize", () => { size(); if (rm.matches) frame(8000); }); size(); pal();
  const frame = ms => { gl.uniform1f(U("t"), ms * .001); gl.uniform1f(U("amp"), amp); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES, 0, 3); };
  const loop = ms => { if (!document.hidden) frame(ms); if (!rm.matches) requestAnimationFrame(loop); };
  if (rm.matches) frame(8000); else requestAnimationFrame(loop);
  rm.addEventListener?.("change", () => { if (!rm.matches) requestAnimationFrame(loop); });
})();

document.querySelectorAll(".card").forEach((card) => {
  card.addEventListener("pointermove", (e) => {
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", e.clientX - r.left + "px");
    card.style.setProperty("--my", e.clientY - r.top + "px");
  });
});
