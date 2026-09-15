// SPDX-License-Identifier: Apache-2.0
function counter(x) { return function(d) { x+=d; return x; }; }
let c=counter(10); print(c(2)); print(c(3));
