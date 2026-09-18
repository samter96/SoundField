/* 빌드 시점에 박히는 환경변수의 타입.

   vite 는 `import.meta.env` 를 제공하지만 tsconfig 에 vite/client 타입이
   들어 있지 않아 tsc 가 모른다. 필요한 것만 여기 적는다
   (vite/client 전체를 켜면 이미지·워커 import 선언까지 딸려 온다). */

interface ImportMetaEnv {
  /** 빌드 표기. src/brand.ts 참고 */
  readonly VITE_BRAND?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
