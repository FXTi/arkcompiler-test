// SPDX-License-Identifier: Apache-2.0
class A { #x=4; get(){return this.#x;} }
print(new A().get());
