// SPDX-License-Identifier: Apache-2.0
let a={x:1}; let b={y:2}; Object.setPrototypeOf(b,a); print(b.x+b.y);
