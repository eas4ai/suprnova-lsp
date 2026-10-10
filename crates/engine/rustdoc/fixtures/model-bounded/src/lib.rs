pub trait Gate {}
pub struct Allowed;
impl Gate for Allowed {}
pub struct Denied;

#[fixture_macros::bounded]
pub struct Post<T> {
    pub value: T,
}

impl<T> Post<T> {
    pub fn source_method(&self) -> u64 {
        1
    }
}
