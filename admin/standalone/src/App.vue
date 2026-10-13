<script setup lang="ts">
import MihomoPage from "./MihomoPage.vue";
</script>

<template>
  <div class="mihomo-standalone">
    <MihomoPage />
  </div>
</template>

<style>
body {
  margin: 0;
  background: #f1f5f9;
}
.mihomo-standalone {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px 16px 40px;
  box-sizing: border-box;
}
</style>
