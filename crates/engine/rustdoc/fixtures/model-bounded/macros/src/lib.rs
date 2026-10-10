use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn bounded(_: TokenStream, input: TokenStream) -> TokenStream {
    let mut output = input;
    output.extend(
        "impl<T: crate::Gate> crate::Post<T> { pub fn gated(&self) -> u64 { 1 } }"
            .parse::<TokenStream>()
            .unwrap(),
    );
    output
}
