use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn model(_: TokenStream, item: TokenStream) -> TokenStream {
    let mut expanded = item;
    expanded.extend(
        "impl Model for Post {
            type Key = u64;
        }
        impl Post {
            pub fn generated_method<T>(&self, value: T) -> Builder<T> { Builder { value } }
            #[doc(hidden)]
            pub fn hidden_method(&self) -> u64 { self.id }
            fn private_method(&self) -> u64 { self.id }
        }"
        .parse::<TokenStream>()
        .expect("fixture macro output is valid Rust"),
    );
    expanded
}
