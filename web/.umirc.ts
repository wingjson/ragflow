/*
 * @Author: wingjson wingjson@outlook.com
 * @Date: 2024-11-28 15:59:09
 * @LastEditors: wingjson wingjson@outlook.com
 * @LastEditTime: 2024-11-28 19:22:18
 * @FilePath: \ragflow014\ragflow\web\.umirc.ts
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
import path from 'path';
import { defineConfig } from 'umi';
import { appName } from './src/conf.json';
import routes from './src/routes';

export default defineConfig({
  title: appName,
  outputPath: 'dist',
  alias: { '@parent': path.resolve(__dirname, '../') },
  npmClient: 'npm',
  base: '/',
  routes,
  publicPath: '/',
  esbuildMinifyIIFE: true,
  icons: {},
  hash: true,
  favicons: ['/logo.svg'],
  clickToComponent: {},
  history: {
    type: 'browser',
  },
  plugins: [
    '@react-dev-inspector/umi4-plugin',
    '@umijs/plugins/dist/tailwindcss',
  ],
  jsMinifier: 'terser',
  lessLoader: {
    modifyVars: {
      hack: `true; @import "~@/less/index.less";`,
    },
  },
  devtool: 'source-map',
  copy: ['src/conf.json'],
  proxy: [
    {
      context: ['/api', '/v1'],
      target: 'http://192.168.107.16:9381/',
      changeOrigin: true,
      ws: true,
      logger: console,
      // pathRewrite: { '^/v1': '/v1' },
    },
  ],

  chainWebpack(memo, args) {
    memo.module.rule('markdown').test(/\.md$/).type('asset/source');

    return memo;
  },
  tailwindcss: {},
});
