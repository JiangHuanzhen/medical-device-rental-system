/**
 * compile.js — 知识库编译验证脚本
 *
 * 教材§2.§3：
 * 检查知识库四层目录结构完整性、文件命名规范、双向链接有效性、基线一致性。
 *
 * 使用方式：node compile.js
 * 在 Obsidian Vault 根目录下运行
 */

const fs = require('fs');
const path = require('path');
const vaultRoot = __dirname;  // 在知识库根目录运行

// ============================================================
// 1. 结构完整性检查
// ============================================================
console.log('\n📁 [1/4] 目录结构完整性检查...');

const requiredDirs = [
  'raw/notes',
  'wiki/summaries',
  'wiki/baselines',
];

let dirErrors = 0;
for (const dir of requiredDirs) {
  const fullPath = path.join(vaultRoot, dir);
  if (!fs.existsSync(fullPath)) {
    console.error(`  ❌ [错误] 缺少目录: ${dir}`);
    dirErrors++;
  } else {
    console.log(`  ✅ 目录存在: ${dir}`);
  }
}

if (dirErrors === 0) {
  console.log('  ✅ 目录结构完整性检查通过');
} else {
  console.error(`  ❌ 存在 ${dirErrors} 个目录缺失`);
}

// 检查archive目录（可选）
const archiveDir = path.join(vaultRoot, 'archive');
if (!fs.existsSync(archiveDir)) {
  console.log('  ⚠️ [提示] archive 目录不存在（未归档时正常）');
}

// ============================================================
// 2. 文件命名规范检查
// ============================================================
console.log('\n📝 [2/4] 文件命名规范检查...');

const timestampPattern = /^\d{8}-\d{4}-.+\.md$/;    // 20260615-1430-需求记录.md
const versionPattern = /^.+-v\d+\.\d+\.md$/;         // SRS-初稿-v2.3.md
const baselinePattern = /^BL-\d{8}-\d{2}\//;         // BL-20260615-01/

let nameIssues = 0;

// 检查 raw/notes 目录
const notesDir = path.join(vaultRoot, 'raw/notes');
if (fs.existsSync(notesDir)) {
  const files = fs.readdirSync(notesDir);
  for (const file of files) {
    if (file.endsWith('.md') && !timestampPattern.test(file)) {
      // 允许 README 等特殊文件
      if (!file.startsWith('README')) {
        console.warn(`  ⚠️ [警告] raw/notes 文件名不规范（应含时间戳）: ${file}`);
        nameIssues++;
      }
    }
  }
}

// 检查 wiki/summaries 目录
const summariesDir = path.join(vaultRoot, 'wiki/summaries');
if (fs.existsSync(summariesDir)) {
  const files = fs.readdirSync(summariesDir);
  for (const file of files) {
    if (file.endsWith('.md') && !versionPattern.test(file)) {
      if (!file.startsWith('README')) {
        console.warn(`  ⚠️ [警告] wiki/summaries 文件名不规范（应含版本号）: ${file}`);
        nameIssues++;
      }
    }
  }
}

if (nameIssues === 0) {
  console.log('  ✅ 文件命名规范检查通过');
} else {
  console.warn(`  ⚠️ 存在 ${nameIssues} 个命名规范建议`);
}

// ============================================================
// 3. 双向链接有效性检查
// ============================================================
console.log('\n🔗 [3/4] 双向链接有效性检查...');

function extractWikiLinks(content) {
  const links = [];
  // 匹配 [[链接名]] 和 [[链接名|显示文本]]
  const regex = /\[\[([^\]#\|]+)(?:#[^\|]*)?(?:\|[^\]]*)?\]\]/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    links.push(match[1].trim());
  }
  return [...new Set(links)];  // 去重
}

function findAllMdFiles(dir) {
  const results = [];
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // 不递归到 baselines 已冻结的目录
      if (!fullPath.includes('baselines')) {
        results.push(...findAllMdFiles(fullPath));
      }
    } else if (entry.name.endsWith('.md')) {
      results.push(fullPath);
    }
  }
  return results;
}

let brokenLinks = 0;
let totalLinks = 0;

// 获取所有.md文件
const searchDirs = ['raw', 'wiki'].filter(d => fs.existsSync(path.join(vaultRoot, d)));
const allFiles = searchDirs.flatMap(d => findAllMdFiles(path.join(vaultRoot, d)));

// 建立所有已存在的文件名索引（不含.md后缀和路径）
const existingFiles = new Set();
for (const f of allFiles) {
  const basename = path.basename(f, '.md');
  existingFiles.add(basename);
}

// 检查每个文件中的链接
for (const f of allFiles) {
  const content = fs.readFileSync(f, 'utf-8');
  const links = extractWikiLinks(content);
  totalLinks += links.length;
  for (const link of links) {
    if (!existingFiles.has(link)) {
      console.warn(`  ⚠️ [断链] "${f}" → [[${link}]] 目标不存在`);
      brokenLinks++;
    }
  }
}

if (brokenLinks === 0) {
  console.log(`  ✅ 双向链接检查通过（共 ${totalLinks} 个链接，全部有效）`);
} else {
  console.warn(`  ⚠️ 存在 ${brokenLinks} 个断链（共 ${totalLinks} 个链接）`);
}

// ============================================================
// 4. 基线一致性检查
// ============================================================
console.log('\n📋 [4/4] 基线一致性检查...');

const baselinesDir = path.join(vaultRoot, 'wiki/baselines');
let baselineIssues = 0;

if (fs.existsSync(baselinesDir)) {
  const baselines = fs.readdirSync(baselinesDir, { withFileTypes: true })
    .filter(d => d.isDirectory() && d.name.startsWith('BL-'));

  if (baselines.length === 0) {
    console.log('  ⚠️ [提示] 暂无基线目录（基线未创立时正常）');
  } else {
    for (const bl of baselines) {
      const blPath = path.join(baselinesDir, bl.name);
      const blFiles = fs.readdirSync(blPath).filter(f => f.endsWith('.md'));

      if (blFiles.length === 0) {
        console.warn(`  ⚠️ [警告] 基线目录 ${bl.name} 为空`);
        baselineIssues++;
      } else {
        console.log(`  ✅ 基线 ${bl.name} 含 ${blFiles.length} 个文档`);
      }
    }
  }
} else {
  console.log('  ⚠️ [提示] baselines 目录不存在（基线未创立时正常）');
}

// ============================================================
// 汇总
// ============================================================
console.log('\n' + '='.repeat(50));
console.log('📊 compile.js 编译报告');
console.log('='.repeat(50));

const isPass = dirErrors === 0 && nameIssues <= 2;  // 允许少量命名警告
console.log(`目录完整性: ${dirErrors > 0 ? '❌ 有误' : '✅ 通过'}`);
console.log(`命名规范: ${nameIssues > 0 ? `⚠️ ${nameIssues}个建议` : '✅ 通过'}`);
console.log(`双向链接: ${brokenLinks > 0 ? `⚠️ ${brokenLinks}个断链` : '✅ 通过'}`);
console.log(`基线一致: ${baselineIssues > 0 ? `⚠️ ${baselineIssues}个问题` : '✅ 通过'}`);

console.log('');
if (isPass) {
  console.log('✅ 编译通过！知识库状态正常。');
} else {
  console.log('❌ 编译未通过，请修复以上错误后重新运行。');
}
console.log('');
