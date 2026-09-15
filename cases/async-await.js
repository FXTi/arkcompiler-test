async function value() {
  return await Promise.resolve(7);
}
value().then((x) => print(x));
