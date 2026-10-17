// Real-browser regression against session_refresh_fixture.py and synthetic accounts only.
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {execFileSync}=require('node:child_process');
const {chromium}=require('C:/Users/gui/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const URL='http://127.0.0.1:18514';
const results=[];
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const context=await browser.newContext();
 const page=await context.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const savedPage=async name=>page.waitForFunction(name=>Object.entries(sessionStorage).some(([key,value])=>key.startsWith('wuma.session.v1:')&&JSON.parse(value).view.page===name),name);
 try {
  await page.goto(URL);
  await page.getByLabel('学生学号',{exact:true}).waitFor({timeout:20000});
  await page.getByLabel('学生学号',{exact:true}).fill('20269851');
  await page.getByLabel('密码',{exact:true}).fill('Test-account-pass-2026');
  await page.getByRole('button',{name:'进入学生端',exact:true}).click();
  await page.getByRole('button',{name:/^任务中心/}).waitFor({timeout:20000});
  await page.getByRole('button',{name:/^任务中心/}).click();
  await page.getByText('智能待办中心',{exact:true}).waitFor();
  await savedPage('任务中心');
  await page.reload();
  await page.getByText('智能待办中心',{exact:true}).waitFor({timeout:20000});
  assert.equal(await page.getByLabel('学生学号',{exact:true}).count(),0);
  results.push('学生刷新保留身份和任务中心');

  await page.getByRole('button',{name:'继续答辩',exact:true}).click();
  await page.getByText('问题2',{exact:true}).waitFor({timeout:20000});
  const answer=page.getByLabel('请输入你的回答',{exact:true});
  assert.ok(['第二题刷新前已保存的草稿','浏览器验收：第二题修改后的草稿'].includes(await answer.inputValue()));
  await answer.fill('浏览器验收：第二题修改后的草稿');
  await page.getByText('问题2',{exact:true}).click();
  await savedPage('AI答辩');
  // A control rerun acknowledges the server-side draft callback before refresh.
  await page.getByRole('button',{name:/^任务中心/}).click();
  await page.getByText('智能待办中心',{exact:true}).waitFor();
  await page.getByRole('button',{name:'继续答辩',exact:true}).click();
  await page.getByText('问题2',{exact:true}).waitFor();
  await savedPage('AI答辩');
  await page.reload();
  await page.getByText('问题2',{exact:true}).waitFor({timeout:20000});
  assert.equal(await page.getByLabel('请输入你的回答',{exact:true}).inputValue(),'浏览器验收：第二题修改后的草稿');
  results.push('学生刷新恢复第2题和已保存的修改草稿');

  const otherTab=await context.newPage();
  await otherTab.goto(URL);
  await otherTab.getByLabel('学生学号',{exact:true}).waitFor({timeout:20000});
  assert.equal(await otherTab.getByRole('button',{name:'退出登录',exact:true}).count(),0);
  await otherTab.close();
  results.push('独立新标签页不自动继承登录');

  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await page.getByLabel('学生学号',{exact:true}).waitFor();
  await page.waitForFunction(()=>!Object.keys(sessionStorage).some(k=>k.startsWith('wuma.session.v1:')));
  await page.reload();
  await page.getByLabel('学生学号',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'退出登录',exact:true}).count(),0);
  results.push('退出后刷新保持退出、清除本页会话');

  await page.getByText('教师入口',{exact:true}).click();
  await page.getByLabel('教师账号',{exact:true}).fill('refresh_teacher');
  await page.getByLabel('密码',{exact:true}).fill('Test-account-pass-2026');
  await page.getByRole('button',{name:'进入教师端',exact:true}).click();
  await page.getByRole('button',{name:/^教师工作台/}).waitFor({timeout:20000});
  await page.getByRole('button',{name:/^教师工作台/}).click();
  await savedPage('教师工作台');
  await page.reload();
  await page.getByRole('button',{name:'退出登录',exact:true}).waitFor({timeout:20000});
  await savedPage('教师工作台');
  assert.equal(await page.getByLabel('教师账号',{exact:true}).count(),0);
  assert.match(await page.locator('body').innerText(),/刷新测试教师/);
  results.push('教师刷新保留身份和教师工作台');

  const metadata=JSON.parse(readFileSync('tmp/browser_fixture.json','utf8'));
  assert.match(metadata.database,/wuma-browser-regression-/);
  execFileSync('D:/AI_Coding/.venv/Scripts/python.exe',['-c',"import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE accounts SET active=0 WHERE username=?',('refresh_teacher',)); c.commit()",metadata.database],{windowsHide:true});
  await page.reload();
  await page.getByText('会话已过期或被撤销，请重新登录。已保存草稿仍可恢复。',{exact:true}).waitFor({timeout:20000});
  await page.getByLabel('学生学号',{exact:true}).waitFor();
  await page.waitForFunction(()=>!Object.keys(sessionStorage).some(k=>k.startsWith('wuma.session.v1:')));
  results.push('停用账号后刷新拒绝恢复并清理凭证');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:results.length,results,pageErrors:errors}));
 } catch(e) {
  // Never include passwords, sessionStorage payloads or authenticated URLs in diagnostics.
  console.error(JSON.stringify({completed:results,buttons:await page.getByRole('button').allTextContents(),headings:await page.getByRole('heading').allTextContents()}));
  throw e;
 } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
