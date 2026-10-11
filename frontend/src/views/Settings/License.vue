<template>
  <div class="license-page">
    <div class="header">
      <h2>授权管理</h2>
      <p>当前为社区版，不提供付费授权解锁。</p>
    </div>
    <el-card shadow="never">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="版本">社区版</el-descriptions-item>
        <el-descriptions-item label="学员等级">初级学员</el-descriptions-item>
        <el-descriptions-item label="分析流">社区版不可用</el-descriptions-item>
      </el-descriptions>
      <p class="note">
        授权页只说明当前版本能力。分析流等高级功能不会再跳转到无效地址。
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
</script>

<style scoped>
.license-page {
  padding: 16px;
}
.header h2 {
  margin: 0 0 8px;
}
.header p,
.note {
  color: #606266;
  line-height: 1.7;
}
.note {
  margin-top: 16px;
}
</style>
