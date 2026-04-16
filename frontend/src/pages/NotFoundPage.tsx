export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-50 dark:bg-gray-900">
      <h1 className="text-6xl font-bold text-gray-300 dark:text-gray-600">404</h1>
      <p className="mt-4 text-lg text-gray-500 dark:text-gray-400">ページが見つかりません</p>
      <a href="/" className="mt-6 text-terracotta-600 hover:text-terracotta-500 dark:text-terracotta-400">
        ホームに戻る
      </a>
    </div>
  );
}
