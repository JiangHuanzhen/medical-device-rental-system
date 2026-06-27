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

// 可选目录
for (const dir of ['archive', 'raw/agents']) {
  if (fs.existsSync(path.join(vaultRoot, dir))) {
    console.log(`  ✅ 目录存在: ${dir}`);
  } else {
    console.log(`  ⚠️ [可选] ${dir} 不存在（不影响编译通过）`);
  }
}

if (dirErrors === 0) {
  console.log('  ✅ 目录结构完整性检查通过');
} else {
  // baselines 不存在是正常的（基线未创立时）
  if (dirErrors === 1 && !fs.existsSync(path.join(vaultRoot, 'wiki/baselines'))) {
    console.log('  ⚠️ [提示] baselines 不存在（基线未创立时正常，不影响编译通过）');
    dirErrors = 0;  // 不算错误
  } else {
    console.error(`  ❌ 存在 ${dirErrors} 个目录缺失`);
  }
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

const timestampPattern = /^.+-\d{8}-\d{4}[-\.]/;         // 招商业务员-20260622-1205-需求记录.md
const versionPattern = /^.+-v\d+(\.\d+)?\.md$/;           // SRS-初稿-v2.3.md 或 需求清单-20260626-v1.md

let nameIssues = 0;

// 检查 raw/notes 目录
const notesDir = path.join(vaultRoot, 'raw/notes');
if (fs.existsSync(notesDir)) {
  const files = fs.readdirSync(notesDir);
  for (const file of files) {
    if (file.endsWith('.md') && !timestampPattern.test(file)) {
      // 允许 README、Agent定义 等特殊文件
      if (!file.startsWith('README') && !file.startsWith('Agent定义')) {
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
    } else if (entry.name.endsWith('.md') || entry.name.endsWith('.puml') || entry.name.endsWith('.png')) {
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
  console.warn(`  ⚠️ 存在 ${brokenLinks} 个断链（共 ${totalLinks} 个链接，涉众需求记录断链为正常——A1运行后生成）`);
}

// ============================================================
// 4. 基线一致性检查
// ============================================================
console.log('\n📋 [4/4] 基线一致性检查...');

const baselinesDir = path.join(vaultRoot, 'wiki/baselines');
let baselineIssues = 0;

if (fs.existsSync(baselinesDir)) {
  const baselines = fs.readdirSync(baselinesDir, { withFileTypes: true })
    .filter(d => d.isDirectory() && d.name.startsWith('BL-'))
    .sort((a, b) => b.name.localeCompare(a.name));  // 最新基线在前

  if (baselines.length === 0) {
    console.log('  ⚠️ [提示] 暂无基线目录（基线未创立时正常）');
  } else {
    const latestBL = baselines[0];
    const blPath = path.join(baselinesDir, latestBL.name);

    // 基线 → summaries 文档对应关系
    const docMap = [
      { blPattern: /SRS-正式版/, summaryPattern: /SRS-初稿/, name: 'SRS' },
      { blPattern: /需求清单/,    summaryPattern: /需求清单/,     name: '需求清单' },
    ];

    for (const { blPattern, summaryPattern, name } of docMap) {
      // 在基线目录中查找对应文件
      const blFiles = fs.readdirSync(blPath).filter(f => blPattern.test(f));
      const blFile = blFiles.length > 0 ? path.join(blPath, blFiles[0]) : null;

      // 在 summaries 目录中查找最新版本
      const summariesDir = path.join(vaultRoot, 'wiki/summaries');
      let summaryFile = null;
      if (fs.existsSync(summariesDir)) {
        const matches = fs.readdirSync(summariesDir)
          .filter(f => summaryPattern.test(f))
          .sort((a, b) => b.localeCompare(a));  // 最新版本在前
        summaryFile = matches.length > 0 ? path.join(summariesDir, matches[0]) : null;
      }

      if (blFile && summaryFile && fs.existsSync(blFile) && fs.existsSync(summaryFile)) {
        // 比对核心内容（取前2000字，排除日期/版本号等元数据差异）
        const blContent = fs.readFileSync(blFile, 'utf-8')
          .replace(/\d{8}/g, '')      // 去日期
          .replace(/v\d+(\.\d+)?/g, '')  // 去版本号
          .substring(0, 2000);
        const summaryContent = fs.readFileSync(summaryFile, 'utf-8')
          .replace(/\d{8}/g, '')
          .replace(/v\d+(\.\d+)?/g, '')
          .substring(0, 2000);

        if (blContent === summaryContent) {
          console.log(`  ✅ ${name}: 基线 ↔ summaries 一致`);
        } else {
          console.warn(`  ⚠️ [警告] ${name}: summaries 版本与基线不一致，可能已被修改但未创立新基线`);
          baselineIssues++;
        }
      } else if (blFile && !summaryFile) {
        console.log(`  ⚠️ [提示] ${name}: summaries 中无对应文件（已基线化，正常）`);
      } else if (!blFile && summaryFile) {
        console.log(`  ⚠️ [提示] ${name}: summaries 存在但基线中无对应文件`);
      } else {
        console.log(`  ⚠️ [提示] ${name}: 未找到基线或 summaries 文件`);
      }

      // 检查基线 SRS 内部一致性：关键章节是否存在
      if (blFile && name === 'SRS') {
        const srsContent = fs.readFileSync(blFile, 'utf-8');
        const requiredSections = ['# 1', '# 2', '# 3', '# 4', '修订历史记录'];
        for (const section of requiredSections) {
          if (!srsContent.includes(section)) {
            console.warn(`  ⚠️ [警告] SRS 缺少关键章节: ${section}`);
            baselineIssues++;
          }
        }
        // 检查 Mermaid 架构图
        if (srsContent.includes('flowchart') || srsContent.includes('graph TD')) {
          console.log('  ✅ SRS 含系统架构图（Mermaid）');
        } else {
          console.warn('  ⚠️ [警告] SRS 缺少系统架构图（Mermaid 代码）');
          baselineIssues++;
        }
        // 检查 E-R 图
        if (srsContent.includes('erDiagram')) {
          console.log('  ✅ SRS 含 E-R 图（Mermaid erDiagram）');
        } else {
          console.warn('  ⚠️ [警告] SRS 缺少 E-R 图（Mermaid erDiagram）');
          baselineIssues++;
        }
        // 检查 PlantUML 用例图
        if (srsContent.includes('@startuml') && srsContent.includes('@enduml')) {
          console.log('  ✅ SRS 含用例图（PlantUML）');
        } else {
          console.warn('  ⚠️ [警告] SRS 缺少用例图（PlantUML 代码）');
          baselineIssues++;
        }
      }
    }

    console.log(`  ✅ 基线 ${latestBL.name} 检查完成（${latestBL.name} 个文档对比）`);
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
// baselines 不存在不阻塞（基线未创立时正常）
const baselineOk = baselineIssues === 0;
const linkOk = brokenLinks <= 4;  // 涉众需求记录断链正常——A1运行后生成
console.log(`目录完整性: ${dirErrors > 0 ? '❌ 有误' : '✅ 通过'}`);
console.log(`命名规范: ${nameIssues > 0 ? `⚠️ ${nameIssues}个建议` : '✅ 通过'}`);
console.log(`双向链接: ${brokenLinks > 0 ? `⚠️ ${brokenLinks}个断链(涉众需求记录断链正常)` : '✅ 通过'}`);
console.log(`基线一致: ${baselineOk ? '✅ 通过' : `⚠️ ${baselineIssues}个问题`}`);

console.log('');
if (isPass && linkOk) {
  console.log('✅ 编译通过！知识库状态正常。');
} else {
  console.log('❌ 编译未通过，请修复以上错误后重新运行。');
}
console.log('');
