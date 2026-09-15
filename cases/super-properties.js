// SPDX-License-Identifier: Apache-2.0
class A { constructor(){this.x=2;} } class B extends A { get(){return super.x ?? this.x;} }
print(new B().get());
