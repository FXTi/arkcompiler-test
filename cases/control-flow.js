// SPDX-License-Identifier: Apache-2.0
function sum(n) { let s=0; for(let i=0;i<n;i++) { if(i%2===0) s+=i; else s-=i; } return s; }
print(sum(10));
