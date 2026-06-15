## ADDED Requirements

### Requirement: 用户登录认证
系统 SHALL 支持用户名+密码登录认证，登录后返回 JWT Token 用于后续请求鉴权。

#### Scenario: 成功登录
- **WHEN** 用户输入正确的用户名和密码并点击登录
- **THEN** 系统返回 JWT Token 并跳转至首页

#### Scenario: 密码错误登录失败
- **WHEN** 用户输入错误的密码
- **THEN** 系统返回"用户名或密码错误"提示，不生成 Token

### Requirement: 用户创建与管理
系统 SHALL 支持管理员创建和管理用户，包含用户名、密码、角色（招商业务员/库房人员/运维工程师/财务）、姓名、联系方式等字段。

#### Scenario: 创建用户
- **WHEN** 管理员提交用户信息（用户名、密码、角色、姓名）
- **THEN** 系统创建用户并返回用户基本信息

#### Scenario: 编辑用户
- **WHEN** 管理员修改用户信息或角色
- **THEN** 系统更新用户信息

### Requirement: 角色权限控制
系统 SHALL 支持四种角色：招商业务员（business_dev）、库房人员（warehouse）、运维工程师（maintenance）、财务（finance）。不同角色可访问不同功能模块和操作按钮。

#### Scenario: 招商业务员访问租赁管理
- **WHEN** 招商业务员登录系统
- **THEN** 系统展示客户管理、创建租赁订单等菜单项

#### Scenario: 库房人员访问设备管理
- **WHEN** 库房人员登录系统
- **THEN** 系统展示设备管理、出入库等菜单项

#### Scenario: 运维工程师访问维修管理
- **WHEN** 运维工程师登录系统
- **THEN** 系统展示设备维修记录菜单项

#### Scenario: 财务访问费用结算
- **WHEN** 财务角色登录系统
- **THEN** 系统展示费用结算、收入统计等菜单项

#### Scenario: 无权限访问受限
- **WHEN** 招商业务员尝试访问费用结算页面
- **THEN** 系统拒绝访问并返回无权限提示

### Requirement: 用户退出登录
系统 SHALL 支持用户退出登录，清除当前会话的 Session/Token。

#### Scenario: 用户退出
- **WHEN** 已登录用户点击退出按钮
- **THEN** 系统清除 Session 并跳转至登录页

### Requirement: 密码修改
系统 SHALL 支持已登录用户修改自己的密码。

#### Scenario: 修改密码成功
- **WHEN** 用户输入原密码和新密码并提交
- **THEN** 系统验证原密码正确后更新为新密码
