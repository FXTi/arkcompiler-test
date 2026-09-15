// SPDX-License-Identifier: Apache-2.0
function f(x) { try { if(x) throw 7; return 3; } catch(e) { return e+1; } finally { print("finally"); } }
print(f(false)); print(f(true));
