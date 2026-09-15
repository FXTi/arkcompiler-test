let target = {x: 1};
let p = new Proxy(target, {get(t, k) { return t[k] + 1; }});
print(p.x);
