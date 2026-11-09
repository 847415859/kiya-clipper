<template>
  <el-form label-width="132px" size="small" class="style-form">
    <el-form-item v-for="(p, key) in props.schema.properties" :key="key" :label="p.title || key">
      <div class="field">
        <el-switch v-if="p.type === 'boolean'" v-model="model[key]" />

        <el-input-number v-else-if="p.type === 'integer' || p.type === 'number'" v-model="model[key]"
                         :min="p.minimum" :max="p.maximum" :step="p.type === 'integer' ? 1 : step(p)"
                         :precision="p.type === 'integer' ? 0 : undefined" controls-position="right" />

        <div v-else-if="p.format === 'color'" class="color">
          <el-color-picker :model-value="toPicker(model[key])" show-alpha color-format="hex"
                           @change="(v) => (model[key] = fromPicker(v, model[key]))" />
          <el-input v-model="model[key]" style="width: 130px" placeholder="#RRGGBB(AA)" />
        </div>

        <el-select v-else-if="p.enum" v-model="model[key]" style="width: 160px">
          <el-option v-for="(v, i) in p.enum" :key="v" :value="v" :label="(p.enumNames && p.enumNames[i]) || v" />
        </el-select>

        <el-select v-else-if="p.type === 'array'" v-model="model[key]" multiple filterable allow-create
                   default-first-option :reserve-keyword="false" placeholder="输入后回车添加" style="width: 100%" />

        <el-input v-else v-model="model[key]" />
        <div v-if="p.description" class="muted desc">{{ p.description }}</div>
      </div>
    </el-form-item>
  </el-form>
</template>

<script setup>
// 按 config/schema/subtitle_style.schema.json 自动生成表单；后端会再按同一份 schema 校验
const props = defineProps({ schema: { type: Object, required: true } })
const model = defineModel({ type: Object, required: true })

function step(p) {
  const range = (p.maximum ?? 10) - (p.minimum ?? 0)
  return range <= 5 ? 0.1 : 0.5
}

// 颜色：配置里是 #RRGGBBAA（AA 为不透明度），取色器给的可能是 #rrggbbaa 或 rgba(...)
function toPicker(v) {
  return v || '#FFFFFF'
}

function fromPicker(v, old) {
  if (!v) return old
  const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/i.exec(v)
  const hex = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')
  if (m) {
    const a = m[4] === undefined ? 1 : parseFloat(m[4])
    return ('#' + hex(+m[1]) + hex(+m[2]) + hex(+m[3]) + (a < 1 ? hex(a * 255) : '')).toUpperCase()
  }
  const h = String(v).replace('#', '')
  if (h.length === 8 && h.slice(6).toLowerCase() === 'ff') return ('#' + h.slice(0, 6)).toUpperCase()
  return ('#' + h).toUpperCase()
}
</script>

<style scoped>
.field { width: 100%; }
.color { display: flex; gap: 8px; align-items: center; }
.desc { line-height: 1.4; margin-top: 2px; }
.style-form :deep(.el-form-item) { margin-bottom: 10px; }
</style>
