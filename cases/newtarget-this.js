// SPDX-License-Identifier: Apache-2.0
class A { constructor(){ print(new.target === A); print(this instanceof A); } } new A();
