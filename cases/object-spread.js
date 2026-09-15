// SPDX-License-Identifier: Apache-2.0
let a={x:1,y:2}; let b={...a,z:3}; let {x,...r}=b; print(x+r.y+r.z);
