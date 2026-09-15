// SPDX-License-Identifier: Apache-2.0
class A { constructor(x) { this.x=x; } get value() { return this.x; } set value(x) { this.x=x; } }
class B extends A { value2() { return this.value*2; } }
let b=new B(5); b.value=8; print(b.value2());
