/**
 * 微信 PC 端小程序包解密（V1MMWX 容器）。
 *
 * 这是本次逆向中**已验证可靠**的那一步：解开微信 PC 端的加密封装，
 * 得到标准 wxapkg 明文。明文里包含全部编译产物的字符串与代码，
 * 用 grep / strings 即可定位接口与签名逻辑（本次分析正是这样做的）。
 *
 * 容器结构：
 *   "V1MMWX"                6 字节明文魔数
 *   AES-128-CBC 密文        1024 字节
 *   XOR 混淆                其余全部字节
 *
 *   AES key = PBKDF2-HMAC-SHA1(password=appid, salt="saltiest",
 *                               iterations=1000, dkLen=32) 的前 16 字节
 *   AES IV  = "the iv: 16 bytes"
 *   XOR key = appid 的倒数第二个字符
 *
 * 明文头部实际只有 1023 字节，第 1024 字节是被替换掉的填充，需丢弃。
 *
 * 用法: node tools/decrypt-package.mjs <__APP__.wxapkg> <appid> <out.wxapkg>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { pbkdf2Sync, createDecipheriv } from 'node:crypto';

const [, , inPath, appid, outPath] = process.argv;
if (!inPath || !appid || !outPath) {
  console.error('usage: node tools/decrypt-package.mjs <__APP__.wxapkg> <appid> <out.wxapkg>');
  process.exit(2);
}

const MAGIC = 'V1MMWX';
const AES_HEADER_LEN = 1024;

const enc = readFileSync(inPath);
if (enc.subarray(0, MAGIC.length).toString('latin1') !== MAGIC) {
  throw new Error(
    `不是 V1MMWX 加密包（前 6 字节为 ${JSON.stringify(enc.subarray(0, 6).toString('latin1'))}）`,
  );
}

const derived = pbkdf2Sync(
  Buffer.from(appid, 'utf8'),
  Buffer.from('saltiest', 'utf8'),
  1000,
  32,
  'sha1',
);

const decipher = createDecipheriv(
  'aes-128-cbc',
  derived.subarray(0, 16),
  Buffer.from('the iv: 16 bytes', 'utf8'),
);
decipher.setAutoPadding(false);

const headStart = MAGIC.length;
const headEnd = MAGIC.length + AES_HEADER_LEN;
const head = Buffer.concat([decipher.update(enc.subarray(headStart, headEnd)), decipher.final()]);

const xorKey = appid.length >= 2 ? appid.charCodeAt(appid.length - 2) : 0x66;
const tail = enc.subarray(headEnd);
const body = Buffer.allocUnsafe(tail.length);
for (let i = 0; i < tail.length; i++) body[i] = tail[i] ^ xorKey;

const plain = Buffer.concat([head.subarray(0, AES_HEADER_LEN - 1), body]);
writeFileSync(outPath, plain);

console.log(`appid      : ${appid}`);
console.log(`xor key    : 0x${xorKey.toString(16)} ('${String.fromCharCode(xorKey)}')`);
console.log(`解密后大小 : ${plain.length} 字节`);
console.log(`输出       : ${outPath}`);
console.log('');
console.log('提示：解出的文件可直接检索到业务接口与签名逻辑，例如');
console.log("  grep -a 'FlySource-sign' <out.wxapkg>");
console.log("  grep -a 'dormSignRecord/stuSign' <out.wxapkg>");
console.log('');
console.log('注：wxapkg 的文件索引在本项目样本里并非连续存放');
console.log('（前 48 条记录连续，末条夹在数据区中间），因此本工具只做解密，');
console.log('不做按文件切分——分析明文已经足够。');
