<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<style>
:root {
  --trip-primary: #2f6f5f;
  --trip-primary-hover: #25594d;
  --trip-heading: #22332e;
  --trip-border: #e4ebe6;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: #f5f7fa;
}

#app {
  min-height: 100vh;
}

.home-container .el-button--primary,
.result-container .el-button--primary {
  border-color: var(--trip-primary);
  background: var(--trip-primary);
}

.home-container .el-button--primary:hover,
.result-container .el-button--primary:hover {
  border-color: var(--trip-primary-hover);
  background: var(--trip-primary-hover);
}

.home-container .card-header,
.result-container .card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #243b35;
  font-size: 17px;
  font-weight: 700;
}
</style>
