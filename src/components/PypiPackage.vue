<script setup>
import { reactive, watch, defineEmits } from 'vue';
import { getPackageInfo, getReadme } from '../lib/pypiClient.js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';

const props = defineProps({
  name: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['listConnections']);

const pkgInfo = reactive({
  state: 'LOADING',
  name: '',
  summary: '',
  version: '',
  license: '',
  homepage: '',
});

const readmeInfo = reactive({
  state: 'UNAVAILABLE',
  content: '',
});

async function fetchPackageInfo() {
  pkgInfo.state = 'LOADING';
  const data = await getPackageInfo(props.name);
  Object.assign(pkgInfo, data);
}

async function fetchReadme() {
  readmeInfo.state = 'LOADING';
  const data = await getReadme(props.name);
  if (data.state === 'LOADED') {
    const html = marked.parse(data.content || '');
    data.content = DOMPurify.sanitize(html);
  }
  Object.assign(readmeInfo, data);
}

watch(() => props.name, () => {
  fetchPackageInfo().then(fetchReadme);
}, { immediate: true });

function listConnections() {
  emit('listConnections');
}
</script>

<template>
  <div class="package-viewer">
    <h2>
      <a :href="`https://pypi.org/project/${props.name}/`" target="_blank">{{ props.name }}</a>
      <small v-if="pkgInfo.version">&nbsp;v{{ pkgInfo.version }}</small>
    </h2>

    <div v-if="pkgInfo.state === 'LOADED'">
      <div class="pkg-summary">{{ pkgInfo.summary }}</div>
      <div class="pkg-meta">
        <span v-if="pkgInfo.license">License: {{ pkgInfo.license }}</span>
        <span v-if="pkgInfo.homepage">&nbsp;•&nbsp;<a :href="pkgInfo.homepage" target="_blank">Homepage</a></span>
      </div>
    </div>

    <div v-if="pkgInfo.state === 'ERROR'" class="error">{{ pkgInfo.error }}</div>
    <div v-if="pkgInfo.state === 'NOT_FOUND'" class="error">Package not found.</div>

    <div class="actions">
      <a href="#" @click.prevent="listConnections">List connections</a>
    </div>

    <div class="readme" v-if="readmeInfo.state === 'LOADED'">
      <div v-html="readmeInfo.content"></div>
    </div>
  </div>
</template>

<style scoped>
.package-viewer {
  max-height: 100%;
  overflow-y: auto;
  padding: 8px;
}
.pkg-meta {
  font-size: 14px;
  color: var(--color-text-light, #888);
  margin-top: 4px;
}
.error {
  color: var(--vt-c-red-1);
  margin: 8px 0;
}
.actions {
  margin: 12px 0;
}
.readme {
  padding-top: 12px;
}
</style> 