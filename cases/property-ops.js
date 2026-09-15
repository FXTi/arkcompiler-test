// SPDX-License-Identifier: Apache-2.0
let o={x:1}; o["y"]=2; print(o["x"]+o["y"]); delete o.x; print("x" in o);
