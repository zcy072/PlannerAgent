import { ElMessage } from 'element-plus'
import type { Ref } from 'vue'
import type { TripPlan } from '@/types'
import { fitCanvasScale } from '@/utils/pdfScale'

export function useTripPdf(
  tripPlan: Ref<TripPlan | null>,
  contentRef: Ref<HTMLElement | null>,
  options: { beforeExport?: () => void | Promise<void>; afterExport?: () => void } = {},
) {
  async function exportPdf() {
    ElMessage.info('正在生成PDF...')

    try {
      // Inside the try so afterExport always runs and the page is never left in export mode.
      await options.beforeExport?.()

      const html2canvas = (await import('html2canvas')).default
      const { jsPDF } = await import('jspdf')

      const element = contentRef.value
      if (!element) {
        ElMessage.error('没有可导出的行程内容')
        return
      }

      const canvas = await html2canvas(element, {
        scale: fitCanvasScale(element.scrollWidth, element.scrollHeight),
        useCORS: true,
        logging: false,
      })

      const pdf = new jsPDF('p', 'mm', 'a4')
      const pageMargin = 10
      const pageWidth = 210 - pageMargin * 2
      const pageHeight = 297 - pageMargin * 2
      const imageScale = pageWidth / canvas.width
      const sourcePageHeight = Math.max(1, Math.floor(pageHeight / imageScale))

      let sourceY = 0
      let pageIndex = 0
      while (sourceY < canvas.height) {
        const sliceHeight = Math.min(sourcePageHeight, canvas.height - sourceY)
        const pageCanvas = document.createElement('canvas')
        pageCanvas.width = canvas.width
        pageCanvas.height = sliceHeight

        const context = pageCanvas.getContext('2d')
        if (!context) throw new Error('无法创建 PDF 页面')

        context.fillStyle = '#ffffff'
        context.fillRect(0, 0, pageCanvas.width, pageCanvas.height)
        context.drawImage(
          canvas,
          0,
          sourceY,
          canvas.width,
          sliceHeight,
          0,
          0,
          canvas.width,
          sliceHeight,
        )

        if (pageIndex > 0) pdf.addPage()
        pdf.addImage(
          pageCanvas.toDataURL('image/png'),
          'PNG',
          pageMargin,
          pageMargin,
          pageWidth,
          sliceHeight * imageScale,
        )

        sourceY += sliceHeight
        pageIndex += 1
      }

      pdf.save(`${tripPlan.value?.city || '旅行'}旅行计划.pdf`)

      ElMessage.success('PDF导出成功')
    } catch (error) {
      console.error('PDF导出失败:', error)
      ElMessage.error('PDF导出失败')
    } finally {
      options.afterExport?.()
    }
  }

  return { exportPdf }
}
