Vendored browser libraries for fully offline graph exports:

- Cytoscape.js 3.34.3, MIT. Source: https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.3.tgz (SHA256 `5d9e479216ed58a5f1ac639e746fd7d9770c4393f8c355dd133abc90bcc9ff3f`); extracted `package/dist/cytoscape.min.js` (SHA256 `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91`); upstream `package/LICENSE` in `cytoscape.LICENSE`.
- Apache ECharts 6.1.0, Apache-2.0. Source: https://registry.npmjs.org/echarts/-/echarts-6.1.0.tgz (SHA256 `681aff88cc1038c3fa941a3a53d1b177a52250b05335efc607d6105ae13be481`); extracted `package/dist/echarts.min.js` (SHA256 `b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0`); upstream `package/LICENSE`, `package/NOTICE`, and `package/licenses/LICENSE-d3` are preserved as `echarts.LICENSE`, `echarts.NOTICE`, and `echarts.LICENSE-d3`.

The files are distributed in the wheel and inserted into standalone HTML and reusable viewer JavaScript by `graph.py`. They load locally, without a CDN.
